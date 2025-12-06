from Controller.MTGAController.Controller import Controller
from AI.DummyAI import DummyAI
from Game import Game
import time

def main():
    print("Starting MTG AI Bot...")

    # User configuration
    log_path = "C:/Users/giaco/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"
    
    click_targets = {
        "keep_hand": {
            "x": 1876,
            "y": 1060
        },
        "queue_button": {
            "x": 2485,
            "y": 1194
        },
        "next": {
            "x": 2546,
            "y": 1137
        },
        "concede": {
            "x": 1714,
            "y": 814
        },
        "attack_all": {
            "x": 2529,
            "y": 1131
        },
        "opponent_avatar": {
            "x": 1720,
            "y": 295
        },
        "hand_scan_points": {
            "p1": {
                "x": 994,
                "y": 1255
            },
            "p2": {
                "x": 2421,
                "y": 1253
            }
        }
    }

    # Estimated screen bounds based on coordinates (assuming 2560x1440 or similar)
    # This is important for card casting relative positions
    screen_bounds = ((0, 0), (2560, 1440))

    try:
        # Initialize components
        print(f"Initializing Controller with log path: {log_path}")
        controller = Controller(log_path=log_path, screen_bounds=screen_bounds, click_targets=click_targets)
        
        print("Initializing AI...")
        ai = DummyAI()
        
        print("Initializing Game...")
        game = Game(controller, ai)
        
        print("Starting Game loop...")
        game.start()
        
        # Keep the script running
        while True:
            time.sleep(1)

    except Exception as e:
        print(f"An error occurred: {e}")
        input("Press Enter to exit...")

if __name__ == "__main__":
    main()
