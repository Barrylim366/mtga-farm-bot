# AI BOT CLASS STRUCTURE PLAN
### Classes

Controller Abstract Class
- This class will actually execute actions
- Requires an instance of a child of the AI class to work
    - If none are provided then it will use the dummy AI child
- There will be various child classes however for now the only one we're working on is the MTG Arena controller

AI Abstract Class
- This is the decision maker class
- Drives the bot actions
- There will be various child classes of this abstract class that can be instanced
    - Dummy (All Attack)
    - ML based
    - Other (idk)
- Any object of the AI class can be inserted as a parameter for the instancing of a bot class object