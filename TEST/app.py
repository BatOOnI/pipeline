import pygame
import random
import sys

# Constants
WINDOW_WIDTH, WINDOW_HEIGHT = 800, 600
CELL_SIZE = 20
GRID_WIDTH = WINDOW_WIDTH // CELL_SIZE
GRID_HEIGHT = WINDOW_HEIGHT // CELL_SIZE
FPS_START = 10
SPEED_INCREASE_INTERVAL = 5

# Directions
UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)
OPPOSITE = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}

class SnakeGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption("Snake")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 36)
        self.reset()

    def reset(self):
        self.snake = [(GRID_WIDTH // 2, GRID_HEIGHT // 2)]
        self.direction = RIGHT
        self.food = self.spawn_food()
        self.score = 0
        self.fruits_eaten = 0
        self.speed = FPS_START
        self.game_over = False

    def spawn_food(self):
        while True:
            pos = (random.randint(0, GRID_WIDTH - 1), random.randint(0, GRID_HEIGHT - 1))
            if pos not in self.snake:
                return pos

    def handle_input(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN:
                # ESC toggles pause
                if event.key == pygame.K_ESCAPE:
                    self.paused = not getattr(self, "paused", False)

                # Restart immediately after game over
                if self.game_over and event.key == pygame.K_r:
                    self.reset()
                    self.paused = False
                    return

                if self.paused or self.game_over:
                    return

                if event.key == pygame.K_UP and self.direction != DOWN:
                    self.direction = UP
                elif event.key == pygame.K_DOWN and self.direction != UP:
                    self.direction = DOWN
                elif event.key == pygame.K_LEFT and self.direction != RIGHT:
                    self.direction = LEFT
                elif event.key == pygame.K_RIGHT and self.direction != LEFT:
                    self.direction = RIGHT
    def update(self):
        if self.game_over:
            return
        head_x, head_y = self.snake[0]
        dir_x, dir_y = self.direction
        new_head = ((head_x + dir_x) % GRID_WIDTH, (head_y + dir_y) % GRID_HEIGHT)
        if new_head in self.snake:
            self.game_over = True
            return
        self.snake.insert(0, new_head)
        if new_head == self.food:
            self.score += 1
            self.fruits_eaten += 1
            self.food = self.spawn_food()
            if self.fruits_eaten % SPEED_INCREASE_INTERVAL == 0:
                self.speed += 1
        else:
            self.snake.pop()

    def draw_grid(self):
        for x in range(0, WINDOW_WIDTH, CELL_SIZE):
            pygame.draw.line(self.screen, (40, 40, 40), (x, 0), (x, WINDOW_HEIGHT))
        for y in range(0, WINDOW_HEIGHT, CELL_SIZE):
            pygame.draw.line(self.screen, (40, 40, 40), (0, y), (WINDOW_WIDTH, y))

    def draw_snake(self):
        for segment in self.snake:
            rect = pygame.Rect(segment[0]*CELL_SIZE, segment[1]*CELL_SIZE, CELL_SIZE, CELL_SIZE)
            pygame.draw.rect(self.screen, (0, 255, 0), rect)

    def draw_food(self):
        rect = pygame.Rect(self.food[0]*CELL_SIZE, self.food[1]*CELL_SIZE, CELL_SIZE, CELL_SIZE)
        pygame.draw.rect(self.screen, (255, 0, 0), rect)

    def draw_score(self):
        # Score + current speed HUD
        speed_text = f"Speed: {self.speed}"
        score_text = f"Score: {self.score}"
        self.screen.blit(self.font.render(score_text, True, (255, 255, 255)), (10, 10))
        self.screen.blit(self.font.render(speed_text, True, (255, 255, 255)), (10, 45))

        # Optional pause indicator
        if getattr(self, "paused", False) and not self.game_over:
            pause_text = "Paused (ESC to resume)"
            self.screen.blit(self.font.render(pause_text, True, (255, 255, 0)), (WINDOW_WIDTH // 2 - 200, WINDOW_HEIGHT // 2 - 20))
    def draw_game_over(self):
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        self.screen.blit(overlay, (0, 0))
        msg1 = self.font.render("Game Over", True, (255, 255, 255))
        msg2 = self.font.render(f"Final Score: {self.score}", True, (255, 255, 255))
        msg3 = self.font.render("Press R to Restart or Esc to Exit", True, (255, 255, 255))
        self.screen.blit(msg1, ((WINDOW_WIDTH - msg1.get_width()) // 2, WINDOW_HEIGHT // 2 - 60))
        self.screen.blit(msg2, ((WINDOW_WIDTH - msg2.get_width()) // 2, WINDOW_HEIGHT // 2 - 20))
        self.screen.blit(msg3, ((WINDOW_WIDTH - msg3.get_width()) // 2, WINDOW_HEIGHT // 2 + 20))

    def run(self):
        # Ensure pause attribute exists
        if not hasattr(self, "paused"):
            self.paused = False

        while True:
            self.handle_input()

            # If paused, still render current frame
            if getattr(self, "paused", False) and not self.game_over:
                self.screen.fill((0, 0, 0))
                self.draw_grid()
                self.draw_snake()
                self.draw_food()
                self.draw_score()
                pygame.display.flip()
                self.clock.tick(self.speed)
                continue

            if not self.game_over:
                self.update()

            # Render
            self.screen.fill((0, 0, 0))
            self.draw_grid()
            self.draw_snake()
            self.draw_food()
            self.draw_score()

            if self.game_over:
                self.draw_game_over()

            pygame.display.flip()
            self.clock.tick(self.speed)
        while True:
            self.handle_input()
            self.update()
            self.screen.fill((0, 0, 0))
            self.draw_grid()
            self.draw_snake()
            self.draw_food()
            self.draw_score()
            if self.game_over:
                self.draw_game_over()
            pygame.display.flip()
            self.clock.tick(self.speed)

if __name__ == "__main__":
    game = SnakeGame()
    game.run()
