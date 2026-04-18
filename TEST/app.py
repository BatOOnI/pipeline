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
        self.highscore_file = "highscore.txt"
        self.high_score = 0
        try:
            with open(self.highscore_file, "r", encoding="utf-8") as f:
                self.high_score = int(f.read().strip() or 0)
        except (FileNotFoundError, ValueError):
            self.high_score = 0
        self.reset()
    def reset(self):
        self.snake = [(GRID_WIDTH // 2, GRID_HEIGHT // 2)]
        self.direction = RIGHT
        self.food = self.spawn_food()
        self.score = 0
        self.fruits_eaten = 0
        self.speed = FPS_START
        self.paused = False
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
                if event.key == pygame.K_ESCAPE:
                    self.paused = not self.paused
                elif self.game_over:
                    if event.key == pygame.K_r:
                        self.reset()
                else:
                    if event.key == pygame.K_UP and self.direction != DOWN:
                        self.direction = UP
                    elif event.key == pygame.K_DOWN and self.direction != UP:
                        self.direction = DOWN
                    elif event.key == pygame.K_LEFT and self.direction != RIGHT:
                        self.direction = LEFT
                    elif event.key == pygame.K_RIGHT and self.direction != LEFT:
                        self.direction = RIGHT
    def update(self):
        if self.game_over or self.paused:
            return

        head_x, head_y = self.snake[0]
        dx, dy = self.direction
        new_head = (head_x + dx, head_y + dy)

        if (
            new_head[0] < 0 or new_head[0] >= GRID_WIDTH or
            new_head[1] < 0 or new_head[1] >= GRID_HEIGHT or
            new_head in self.snake
        ):
            self.game_over = True
            if self.score > self.high_score:
                self.high_score = self.score
                try:
                    with open(self.highscore_file, "w", encoding="utf-8") as f:
                        f.write(str(self.high_score))
                except OSError:
                    pass
            return

        self.snake.insert(0, new_head)

        if new_head == self.food:
            self.score += 1
            self.fruits_eaten += 1
            if self.fruits_eaten % SPEED_INCREASE_INTERVAL == 0:
                self.speed += 1
            self.food = self.spawn_food()
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
        score_text = self.font.render(f"Score: {self.score}", True, (255, 255, 255))
        high_text = self.font.render(f"High Score: {self.high_score}", True, (255, 255, 255))
        speed_text = self.font.render(f"Speed: {self.speed}", True, (255, 255, 255))
        self.screen.blit(score_text, (10, 10))
        self.screen.blit(high_text, (10, 45))
        self.screen.blit(speed_text, (10, 80))
    def draw_game_over(self):
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))
        game_over_text = self.font.render("Game Over", True, (255, 255, 255))
        restart_text = self.font.render("Press R to Restart", True, (255, 255, 255))
        self.screen.blit(game_over_text, (WINDOW_WIDTH // 2 - game_over_text.get_width() // 2, WINDOW_HEIGHT // 2 - 40))
        self.screen.blit(restart_text, (WINDOW_WIDTH // 2 - restart_text.get_width() // 2, WINDOW_HEIGHT // 2 + 10))
    def run(self):
        while True:
            self.handle_input()
            self.update()

            self.screen.fill((0, 0, 0))
            self.draw_grid()
            self.draw_food()
            self.draw_snake()
            self.draw_score()

            if self.paused and not self.game_over:
                pause_text = self.font.render("Paused", True, (255, 255, 255))
                self.screen.blit(pause_text, (WINDOW_WIDTH // 2 - pause_text.get_width() // 2, WINDOW_HEIGHT // 2 - 20))

            if self.game_over:
                self.draw_game_over()

            pygame.display.flip()
            self.clock.tick(self.speed)
if __name__ == "__main__":
    game = SnakeGame()
    game.run()
