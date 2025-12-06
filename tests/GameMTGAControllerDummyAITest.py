import time

from Game import Game as G
from Controller.MTGAController.Controller import Controller as cont
from AI.DummyAI import DummyAI as ai

main_game = G(cont('/home/johndoe/Games/magic-the-gathering-arena/dosdevices/c:/users/johndoe/AppData' +
                   '/LocalLow/Wizards Of The Coast/MTGA/Player.log', screen_bounds=((0, 0), (1920, 1080))), ai())
time.sleep(10)
print("Starting Game")
main_game.start()
