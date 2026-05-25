from collections import defaultdict
class Solution:
    def groupAnagrams(self, strs: List[str]) -> List[List[str]]:
        # loop through strs
        # understand uniqueness (sorted, hashed, etc) to use as key in lookup table
            # don't want to sort each. 
            # how to str1 = str2 if same chars different order?
                # unicode code point
            # unique hash usiing unicode -> list of strs
        
        lookup = defaultdict(list)
        for s in strs:
            uni_hash = [0 for _ in range(26)]

            for c in s:
                uni_hash[ord(c) - ord('a')] +=1
            
            lookup[tuple(uni_hash)].append(s)
        
        return list(lookup.values())
            


        
