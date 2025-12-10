class ManaPool:

    def __init__(self):
        self.__total_mana = {"red": 0, "green": 0, "blue": 0, "black": 0, "white": 0, "generic": 0}
        self.__avail_mana = {"red": 0, "green": 0, "blue": 0, "black": 0, "white": 0, "generic": 0}

    def reset_mana(self):
        for key in self.__avail_mana:
            self.__avail_mana[key] = self.__total_mana[key]

    def __convert_raw_mana_cost_arr_to_standard(self, mana_cost_arr):
        """
        Converts a raw mana cost dict to a standard one

        Raw array
        [
          {
            "color": [
              "ManaColor_" + colorName
            ],
            "count": int
          },
          ...
        ]

        Standard dict
        {
            "red": int,
            "green": int,
            "blue": int,
            "black": int,
            "white": int,
            "generic": int
        }

        Returns:
             Standard dict representing the mana cost
        """
        reformatted_mana_cost_dict = {}
        for color_dict in mana_cost_arr:
            key = color_dict['color'][0][10:].lower()
            reformatted_mana_cost_dict[key] = color_dict['count']
        return reformatted_mana_cost_dict

    def use_mana(self, raw_mana_cost_dict):
        """
        Updates self.__avail_mana with the appropriate mana
        Requires:
            Already have enough mana to use that mana
        Parameters:
            raw_mana_cost_dict: mana to be taken away from available mana
        """
        stand_mana_cost_dict = self.__convert_raw_mana_cost_arr_to_standard(raw_mana_cost_dict)
        
        # 1. Pay specific colored costs
        for key in stand_mana_cost_dict.keys():
            if key != 'generic':
                self.__avail_mana[key] -= stand_mana_cost_dict[key]

        # 2. Pay generic cost
        if 'generic' in stand_mana_cost_dict:
            generic_cost = stand_mana_cost_dict['generic']
            
            # First try to pay with actual generic mana
            if self.__avail_mana['generic'] >= generic_cost:
                self.__avail_mana['generic'] -= generic_cost
                generic_cost = 0
            else:
                generic_cost -= self.__avail_mana['generic']
                self.__avail_mana['generic'] = 0
            
            # If still need to pay generic cost, use other colors
            if generic_cost > 0:
                for key in self.__avail_mana:
                    if key == 'generic': 
                        continue
                    if self.__avail_mana[key] >= generic_cost:
                        self.__avail_mana[key] -= generic_cost
                        generic_cost = 0
                        break
                    else:
                        generic_cost -= self.__avail_mana[key]
                        self.__avail_mana[key] = 0

    def has_mana(self, mana_cost_arr):
        """
        Requires:
            mana_cost_dict must be of raw form
        """
        reformatted_mana_cost_dict = self.__convert_raw_mana_cost_arr_to_standard(mana_cost_arr)
        
        # Create a temporary copy to simulate spending
        temp_avail = self.__avail_mana.copy()
        
        # 1. Check/Pay specific colored costs
        for color, cost in reformatted_mana_cost_dict.items():
            if color == 'generic':
                continue
            if temp_avail.get(color, 0) < cost:
                return False
            temp_avail[color] -= cost
            
        # 2. Check/Pay generic cost with remaining mana
        if 'generic' in reformatted_mana_cost_dict:
            generic_cost = reformatted_mana_cost_dict['generic']
            
            # Use generic mana first
            if temp_avail['generic'] >= generic_cost:
                return True
            else:
                generic_cost -= temp_avail['generic']
                # temp_avail['generic'] = 0 # Not needed for calculation
                
            # Sum up all remaining colored mana
            remaining_colored = sum(val for key, val in temp_avail.items() if key != 'generic')
            
            if remaining_colored < generic_cost:
                return False
                
        return True

    def add_mana(self, color, amount):
        self.__total_mana[color] += amount
        self.__avail_mana[color] += amount

    def get_available_mana(self):
        """Returns a copy of the available mana dict for debugging"""
        return self.__avail_mana.copy()

    def get_total_mana(self):
        """Returns a copy of the total mana dict for debugging"""
        return self.__total_mana.copy()

    def spend_mana(self, amount):
        """
        Spend a generic amount of mana (simplified CMC-based spending).
        Deducts from available mana pool, prioritizing colored mana.
        """
        remaining = amount
        # Spend colored mana first (in order: red, green, blue, black, white)
        for color in ['red', 'green', 'blue', 'black', 'white']:
            if remaining <= 0:
                break
            if self.__avail_mana[color] >= remaining:
                self.__avail_mana[color] -= remaining
                remaining = 0
            else:
                remaining -= self.__avail_mana[color]
                self.__avail_mana[color] = 0
        # Then spend generic if any left
        if remaining > 0 and self.__avail_mana['generic'] >= remaining:
            self.__avail_mana['generic'] -= remaining
