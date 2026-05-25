class Solution:
    def twoSum(self, nums: List[int], target: int) -> List[int]:
        # todo: used wrong lookup logic and should have just used "in"
        lookup = {}
        for i in range(len(nums)):
            second_num = nums[i]
            if (first_num_index := lookup.get(target - second_num, -1)) >= 0 :
                return [first_num_index, i] 
            else:
                lookup[second_num] = i
