# Copyright 2024 TikTok Pte. Ltd.
#
# This source code is licensed under the MIT license found in
# the LICENSE file in the root directory of this source tree.

__all__ = ["APIErrorResponse"]


class APIErrorResponse:
    OK = "ok"
    INVALID_PARAM = "invalid_param"
    INVALID_REQUEST = "invalid_request"
    ACCESS_TOKEN_INVALID = "access_token_invalid"
    TIMEOUT = "timeout"

    def __str__(self):
        return self.value
