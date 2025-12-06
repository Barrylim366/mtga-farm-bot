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
        for key in stand_mana_cost_dict.keys():
            if key != 'generic':
                self.__avail_mana[key] -= stand_mana_cost_dict[key]
        if self.__avail_mana['generic'] <= stand_mana_cost_dict['generic']:
            stand_mana_cost_dict['generic'] -= self.__avail_mana['generic']
            self.__avail_mana['generic'] = 0
            for key in stand_mana_cost_dict.keys():
                if self.__avail_mana[key] <= stand_mana_cost_dict['generic']:
                    stand_mana_cost_dict['generic'] -= self.__avail_mana[key]
                    self.__avail_mana[key] = 0
                else:
                    self.__avail_mana[key] -= stand_mana_cost_dict['generic']
                    stand_mana_cost_dict['generic'] = 0
                if stand_mana_cost_dict['generic'] == 0:
                    break
        else:
            self.__avail_mana['generic'] -= stand_mana_cost_dict['generic']

    def has_mana(self, mana_cost_arr):
        """
        Requires:
            mana_cost_dict must be of raw form
        """
        has_mana = True
        total_generic_count = 0
        reformatted_mana_cost_dict = self.__convert_raw_mana_cost_arr_to_standard(mana_cost_arr)
        for color in reformatted_mana_cost_dict.keys():
            total_generic_count += self.__avail_mana[color]
            if color != 'generic':
                if reformatted_mana_cost_dict[color] > self.__avail_mana[color]:
                    # total_generic_count -= self.__avail_mana[color]
                    has_mana = False
                else:
                    total_generic_count -= reformatted_mana_cost_dict[color]

        if 'generic' in reformatted_mana_cost_dict.keys():
            if total_generic_count < reformatted_mana_cost_dict['generic']:
                has_mana = False
        return has_mana

    def add_mana(self, color, amount):
        self.__total_mana[color] += amount
        self.__avail_mana[color] += amount
