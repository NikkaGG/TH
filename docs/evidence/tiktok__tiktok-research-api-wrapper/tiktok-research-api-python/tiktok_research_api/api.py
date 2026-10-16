# Copyright 2024 TikTok Pte. Ltd.
#
# This source code is licensed under the MIT license found in
# the LICENSE file in the root directory of this source tree.

import requests
import urllib
import json
import sys
import logging
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from .errors import *

__all__ = ["TikTokResearchAPI"]

class TikTokResearchAPI:
    def __init__(self, client_key, client_secret,qps=10):
        if not client_key:
            raise ValueError("client_key is required")
        if not client_secret:
            raise ValueError("client_secret is required")
        if qps <= 0:
            raise ValueError("qps must be a positive number")

        logging.basicConfig(level=logging.INFO)
        self.url = "https://open.tiktokapis.com"
        self.client_key = client_key
        self.client_secret = client_secret
        self.client_token = self.get_client_token()
        self.qps = qps
        self.requests = 0
        self.start_time = datetime.now()

    def headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.client_token}",
        }

    def _is_response_valid(self, response):
        try:
            error_code = response.json().get("error", {}).get("code", None)
            error_msg = response.json().get("error", {}).get("message", None)
            log_id = response.json().get("error", {}).get("log_id", None)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if error_code != APIErrorResponse.OK:
                logging.error(f"{timestamp} ERROR::API call error: {error_code}: {error_msg} (Log ID: {log_id})")
                return False
            return True
        except Exception as e:
             logging.error(
                    f"[fetch data] received exception parsing json exception={e}, response body={response.text}, response_headers={response.headers}"
                )
             return False
    def get_client_token(self):
        endpoint = f"{self.url}/v2/oauth/token/"
        token_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        token_payload = {
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        response = requests.post(
            endpoint, headers=token_headers, data=urllib.parse.urlencode(token_payload)
        )
        response.raise_for_status()
        get_token_response = response.json()
        error_code = get_token_response.get("error", None)
        if error_code is not None and error_code != APIErrorResponse.OK:
            description = get_token_response.get("error_description")
            raise Exception(
                f"Error fetching client token: error code={error_code}, error description={description}"
            )
        access_token = get_token_response.get("access_token")
        return access_token

    def rate_limiter(self):
        current_time = datetime.now()
        elapsed_time = (current_time - self.start_time).total_seconds()

        if elapsed_time > 1:
            # Reset the counter and start time if the time window has passed
            self.requests = 0
            self.start_time = current_time
        
        if self.requests >= self.qps:
            # Enforce delay if the limit is reached
            wait_time = 1 - elapsed_time
            logging.info(f"Rate limit reached. Waiting for {wait_time:.2f} seconds...")
            time.sleep(wait_time)
            # Reset the counter and start time after waiting
            self.requests = 0
            self.start_time = datetime.now()
        
        self.requests += 1

    def refresh_token(self):
        self.client_token = self.get_client_token()

    def query_videos(self, video_request, fetch_all_pages=None):
        max_days = 30
        # Convert string dates to datetime objects for internal calculations
        current_start = datetime.strptime(video_request.start_date, "%Y%m%d")
        end_date = datetime.strptime(video_request.end_date, "%Y%m%d")
        
        aggregate_videos = []
        search_id = None
        root_cursor = None
        has_more = False
        max_retries_hit = False
        show_search_id = True
        
        while current_start <= end_date:
            # Break if max_total is reached
            max_total = getattr(video_request, "max_total", 1000000)
            if len(aggregate_videos) >= max_total:
                break
            # Calculate the end date for the current chunk (30 days or less)
            current_end = min(current_start + timedelta(days=max_days - 1), end_date)
            # Convert the datetime objects back to strings for the API request
            start_str = current_start.strftime("%Y%m%d")
            end_str = current_end.strftime("%Y%m%d")
            endpoint = f"{self.url}/v2/research/video/query/?fields={video_request.fields}"
            query_dict = video_request.query.to_dict()
            body = {
                "query": query_dict,
                "start_date": start_str,
                "end_date": end_str,
            }
            optional_fields = ["cursor", "is_random", "search_id", "max_count"]
            optional_fields_provided = {}
            for key in optional_fields:
                value = getattr(video_request, key, None)
                if value is not None:
                    body[key] = value
                    optional_fields_provided[key] = value

            retries = 0
            MAX_RETRIES = 60
            page = 0
            while True:
                self.rate_limiter()
                response = requests.post(endpoint, json=body, headers=self.headers())
                error_code = response.json().get("error", {}).get("code", None)
                error_msg = response.json().get("error", {}).get("message", None)
                if error_code != APIErrorResponse.OK:
                    # Polling while we wait for backend cache to populate
                    retries += 1
                    if retries >= MAX_RETRIES:
                        logging.error(f"{error_msg}")
                        max_retries_hit = True
                        break
                    time.sleep(1)
                    continue

                response_data = response.json().get("data", {})
                videos = response_data.get("videos", [])
                aggregate_videos.extend(videos)
                has_more = response_data.get("has_more", False)
                root_cursor = response_data.get("cursor", None)
                search_id = response_data.get("search_id", None)

                if search_id and show_search_id:
                    logging.info(f"SearchID: {search_id}")                    
                    show_search_id = False

                if not fetch_all_pages or not has_more or len(aggregate_videos) >= max_total:
                    break
                print(f"Page {page} got {len(videos)} videos and has_more {has_more}", flush=True)
                retries = 0  # Reset retries on success
                page += 1

                if root_cursor is not None:
                    body["cursor"] = root_cursor
                    body["search_id"] = search_id

            if max_retries_hit:
                break
            else:
                    # Move to the next date chunk
                current_start = current_end + timedelta(days=1)
        
        return aggregate_videos, search_id, root_cursor, has_more, start_str, end_str

    def query_user_info(self, user_info_request, fetch_all_pages=None):
        endpoint = (
            f"{self.url}/v2/research/user/info/?fields={user_info_request.fields}"
        )
        body = {
            "username": user_info_request.username,
        }
        self.rate_limiter()
        response = requests.post(endpoint, json=body, headers=self.headers())
        if not self._is_response_valid(response):
            return None
        response_data = response.json().get("data", {})
        return response_data

    def query_video_comments(self, comment_info_request, fetch_all_pages=None):
        endpoint = f"{self.url}/v2/research/video/comment/list/?fields={comment_info_request.fields}"
        aggregate_comments = []
        body = {
            "video_id": comment_info_request.video_id,
        }
        optional_fields = ["cursor", "max_count"]
        for key in optional_fields:
            value = getattr(comment_info_request, key)
            if value is not None:
                body[key] = value
        while True:
            self.rate_limiter()
            response = requests.post(endpoint, json=body, headers=self.headers())
            if not self._is_response_valid(response):
                return [], None, False
            response_data = response.json().get("data", {})
            video_comments = response_data.get("comments", [])
            aggregate_comments.extend(video_comments)
            has_more = response_data.get("has_more", False)
            root_cursor = response_data.get("cursor", None)

            if not fetch_all_pages or not has_more:
                break
            if root_cursor is not None:
                body["cursor"] = root_cursor
        return aggregate_comments, root_cursor, has_more

    def query_user_liked_videos(self, user_liked_videos_request, fetch_all_pages=None):
        endpoint = f"{self.url}/v2/research/user/liked_videos/?fields={user_liked_videos_request.fields}"
        aggregate_liked_videos = []
        body = {
            "username": user_liked_videos_request.username,
        }

        optional_fields = ["cursor", "max_count"]
        for key in optional_fields:
            value = getattr(user_liked_videos_request, key)
            if value is not None:
                body[key] = value
    
        while True:
            self.rate_limiter()
            response = requests.post(endpoint, json=body, headers=self.headers())
            if not self._is_response_valid(response):
                return [], None, False
            response_data = response.json().get("data", {})
            user_liked_videos = response_data.get("user_liked_videos", [])
            aggregate_liked_videos.extend(user_liked_videos)
            has_more = response_data.get("has_more", False)
            root_cursor = response_data.get("cursor", None)
            if not fetch_all_pages or not has_more:
                break
            if root_cursor is not None:
                body["cursor"] = root_cursor
        return aggregate_liked_videos, root_cursor, has_more

    def query_user_followers(self, query_user_followers_request, fetch_all_pages=None):
        endpoint = f"{self.url}/v2/research/user/followers/"
        aggregate_followers = []
        body = {
            "username": query_user_followers_request.username,
        }
        optional_fields = ["cursor", "max_count"]
        for key in optional_fields:
            value = getattr(query_user_followers_request, key)
            if value is not None:
                body[key] = value
        while True:
            self.rate_limiter()
            response = requests.post(endpoint, json=body, headers=self.headers())
            if not self._is_response_valid(response):
                return [], None, False
            response_data = response.json().get("data", {})
            user_followers = response_data.get("user_followers", [])
            aggregate_followers.extend(user_followers)
            has_more = response_data.get("has_more", False)
            root_cursor = response_data.get("cursor", None)
            if not fetch_all_pages or not has_more:
                break
            if root_cursor is not None:
                body["cursor"] = root_cursor
        return aggregate_followers, 1, has_more

    def query_user_following(self, query_user_following_request, fetch_all_pages=None):
        endpoint = f"{self.url}/v2/research/user/following/"
        aggregate_following = []
        body = {
            "username": query_user_following_request.username,
        }
        optional_fields = ["cursor", "max_count"]
        for key in optional_fields:
            value = getattr(query_user_following_request, key)
            if value is not None:
                body[key] = value
        while True:
            self.rate_limiter()
            response = requests.post(endpoint, json=body, headers=self.headers())
            if not self._is_response_valid(response):
                return [], None, False
            response_data = response.json().get("data", {})
            user_followers = response_data.get("user_following", [])
            aggregate_following.extend(user_followers)
            has_more = response_data.get("has_more", False)
            root_cursor = response_data.get("cursor", None)
            if not fetch_all_pages or not has_more:
                break
            if root_cursor is not None:
                body["cursor"] = root_cursor
        return aggregate_following, root_cursor, has_more

    def query_user_pinned_videos(self, user_pinned_videos_request):
        endpoint = f"{self.url}/v2/research/user/pinned_videos/?fields={user_pinned_videos_request.fields}"
        aggregate_pinned_videos = []
        body = {
            "username": user_pinned_videos_request.username,
        }
        self.rate_limiter()
        response = requests.post(endpoint, json=body, headers=self.headers())
        if not self._is_response_valid(response):
            return []
        response_data = response.json().get("data", {})
        user_followers = response_data.get("pinned_videos_list", [])
        aggregate_pinned_videos.extend(user_followers)
        return aggregate_pinned_videos

    def query_user_reposted_videos(
        self, reposted_videos_request, fetch_all_pages=False
    ):
        endpoint = f"{self.url}/v2/research/user/reposted_videos/?fields={reposted_videos_request.fields}"
        body = {"username": reposted_videos_request.username}
        optional_fields = ["cursor", "max_count"]
        for key in optional_fields:
            value = getattr(reposted_videos_request, key)
            if value is not None:
                body[key] = value
        aggregated_reposted_videos = []
        while True:
            self.rate_limiter()
            response = requests.post(endpoint, json=body, headers=self.headers())
            if not self._is_response_valid(response):
                return [], None, False
            response_data = response.json().get("data", {})
            videos = response_data.get("reposted_videos", [])
            aggregated_reposted_videos.extend(videos)
            has_more = response_data.get("has_more", False)
            root_cursor = response_data.get("cursor", None)
            if not fetch_all_pages or not has_more:
                break
            if root_cursor is not None:
                body["cursor"] = root_cursor
        return aggregated_reposted_videos, root_cursor, has_more
