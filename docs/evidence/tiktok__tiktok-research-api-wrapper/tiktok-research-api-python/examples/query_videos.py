# Copyright 2024 TikTok Pte. Ltd.
#
# This source code is licensed under the MIT license found in
# the LICENSE file in the root directory of this source tree.

from tiktok_research_api import *
import os

if __name__ == "__main__":
    client_key = 'Your_client_key'
    client_secret = 'Your_client_secret'

    # Initialize the API client
    qps = 5
    research_api = TikTokResearchAPI(client_key, client_secret, qps)

    # Define query object
    query_criteria = Criteria(
        operation="EQ", field_name="hashtag_name", field_values=["tiktok"]
    )
    query = Query(and_criteria=[query_criteria])
    video_request = QueryVideoRequest(
        fields="view_count",
        query=query,
        start_date="20240501",
        end_date="20240630",
        max_count=10,
        max_total=100
    )

    # Query the API for videos matching the request
    videos, search_id, cursor, has_more, start_date, end_date = research_api.query_videos(video_request, fetch_all_pages=True)
    print(search_id, cursor, has_more, start_date, end_date)
    print(videos)
