# Copyright 2024 TikTok Pte. Ltd.
#
# This source code is licensed under the MIT license found in
# the LICENSE file in the root directory of this source tree.

from .criteria import Criteria

__all__ = ["Query"]


class Query:
    def __init__(self, and_criteria=None, or_criteria=None, not_criteria=None):
        self.and_criteria = and_criteria or []
        self.or_criteria = or_criteria or []
        self.not_criteria = not_criteria or []

    def to_dict(self):
        return {
            "and": [criteria.to_dict() for criteria in self.and_criteria],
            "or": [criteria.to_dict() for criteria in self.or_criteria],
            "not": [criteria.to_dict() for criteria in self.not_criteria],
        }
