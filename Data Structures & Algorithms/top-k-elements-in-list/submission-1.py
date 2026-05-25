from collections import defaultdict 

class Solution:
    def topKFrequent(self, nums: List[int], k: int) -> List[int]:
        # count -> hashmap -> val:count
        # top k -> array -> i = count, val = [val1, ...]

        counts = defaultdict(int)
        for num in nums:
            counts[num] += 1

        l = [[] for _ in range(len(nums)+1)]
        for val, count in counts.items():
            l[count].append(val)

        res = []
        for sub_l in l[::-1]:
            res += sub_l
            if len(res) == k:
                return res
             
