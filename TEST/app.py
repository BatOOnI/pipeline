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
        self.high_score = self.load_high_score()
        self.reset()

    def load_high_score(self):
        try:
            with open("highscore.txt", "r") as f:
                return int(f.read().strip())
        except:
            return 0

    def save_high_score(self):
        with open("highscore.txt", "w") as f:
            f.write(str(self.high_score))

    def reset(self):
        self.snake = [(GRID_WIDTH // 2, GRID_HEIGHT // 2)]
        self.direction = RIGHT
        self.food = self.spawn_food()
        self.score = 0
        self.fruits_eaten = 0
        self.speed = FPS_START
        self.game_over = False
        self.paused = False

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
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.paused = not self.paused
                elif event.key == pygame.K_r and self.game_over:
                    self.reset()
                elif not self.paused and not self.game_over:
                    if event.key == pygame.K_UP and self.direction != DOWN:
                        self.direction = UP
                    elif event.key == pygame.K_DOWN and self.direction != UP:
                        self.direction = DOWN
                    elif event.key == pygame.K_LEFT and self.direction != RIGHT:
                        self.direction = LEFT
                    elif event.key == pygame.K_RIGHT and self.direction != LEFT:
                        self.direction = RIGHT

    def update(self):
        if self.paused or self.game_over:
            return
        
        self.snake.insert(0, (self.snake[0][0] + self.direction[0], self.snake[0][1] + self.direction[1]))
        
        if self.snake[0] == self.food:
            self.food = self.spawn_food()
            self.score += 10
            self.fruits_eaten += 1
            if self.fruits_eaten % SPEED_INCREASE_INTERVAL == 0:
                self.speed += 2
        else:
            self.snake.pop()
        
        if (self.snake[0][0] < 0 or self.snake[0][0] >= GRID_WIDTH or
            self.snake[0][1] < 0 or self.snake[0][1] >= GRID_HEIGHT or
            self.snake[0] in self.snake[1:]):
            self.game_over = True
            if self.score > self.high_score:
                self.high_score = self.score
                self.save_high_score()

    def draw_grid(self):
        for x in range(0, WINDOW_WIDTH, CELL_SIZE):
            pygame.draw.line(self.screen, (40, 40, 40), (x, 0), (x, WINDOW_HEIGHT))
        for y in range(0, WINDOW_HEIGHT, CELL_SIZE):
            pygame.draw.line(self.screen, (40, 40, 40), (0, y), (WINDOW_WIDTH, y))

    def draw_snake(self):
        for segment in self.snake:
            rect = pygame.Rect(segment[0] * CELL_SIZE, segment[1] * CELL_SIZE, CELL_SIZE, CELL_SIZE)
            pygame.draw.rect(self.screen, (0, 255, 0), rect)
            pygame.draw.rect(self.screen, (0, 200, 0), rect, 1)

    def draw_food(self):
        rect = pygame.Rect(self.food[0] * CELL_SIZE, self.food[1] * CELL_SIZE, CELL_SIZE, CELL_SIZE)
        pygame.draw.rect(self.screen, (255, 0, 0), rect)

    def draw_score(self):
        score_text = self.font.render(f"Score: {self.score}", True, (255, 255, 255))
        high_score_text = self.font.render(f"High Score: {self.high_score}", True, (255, 255, 255))
        speed_text = self.font.render(f"Speed: {self.speed}", True, (255, 255, 255))
        self.screen.blit(score_text, (10, 10))
        self.screen.blit(high_score_text, (10, 50))
        self.screen.blit(speed_text, (10, 90))

    def draw_game_over(self):
        if self.game_over:
            game_over_text = self.font.render("Game Over! Press R to restart", True, (255, 255, 255))
            score_text = self.font.render(f"Final Score: {self.score}", True, (255, 255, 255))
            high_score_text = self.font.render(f"High Score: {self.high_score}", True, (255, 255, 255))
            self.screen.blit(game_over_text, (WINDOW_WIDTH // 2 - 150, WINDOW_HEIGHT // 2 - 60))
            self.screen.blit(score_text, (WINDOW_WIDTH // 2 - 100, WINDOW_HEIGHT // 2))
            self.screen.blit(high_score_text, (WINDOW_WIDTH // 2 - 100, WINDOW_HEIGHT // 2 + 60))

    def run(self):
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
def run(self):
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
