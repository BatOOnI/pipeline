import sys
import random
import pygame

# Constants
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 600
CELL_SIZE = 20
GRID_WIDTH = WINDOW_WIDTH // CELL_SIZE
GRID_HEIGHT = WINDOW_HEIGHT // CELL_SIZE
FPS_START = 10
SPEED_INCREASE_INTERVAL = 5
SPEED_DECREMENT = 1
MIN_FPS = 5

# Directions
UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)
OPPOSITE = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}

class Snake:
    def __init__(self):
        self.positions = [(GRID_WIDTH // 2, GRID_HEIGHT // 2)]
        self.direction = random.choice([UP, DOWN, LEFT, RIGHT])
        self.grow_pending = False

    def move(self):
        cur_x, cur_y = self.positions[0]
        dir_x, dir_y = self.direction
        new_head = ((cur_x + dir_x) % GRID_WIDTH, (cur_y + dir_y) % GRID_HEIGHT)
        if new_head in self.positions:
            return False  # collision with self
        self.positions.insert(0, new_head)
        if not self.grow_pending:
            self.positions.pop()
        else:
            self.grow_pending = False
        return True

    def change_direction(self, new_dir):
        if new_dir != OPPOSITE[self.direction]:
            self.direction = new_dir

    def grow(self):
        self.grow_pending = True

class Food:
    def __init__(self, snake_positions):
        self.position = self.random_position(snake_positions)

    @staticmethod
    def random_position(snake_positions):
        while True:
            pos = (random.randint(0, GRID_WIDTH - 1), random.randint(0, GRID_HEIGHT - 1))
            if pos not in snake_positions:
                return pos

class Game:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption("Snake")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 36)
        self.reset()

    def reset(self):
        self.snake = Snake()
        self.food = Food(self.snake.positions)
        self.score = 0
        self.fruits_eaten = 0
        self.fps = FPS_START
        self.game_over = False

    def draw_cell(self, position, color):
        rect = pygame.Rect(position[0] * CELL_SIZE, position[1] * CELL_SIZE, CELL_SIZE, CELL_SIZE)
        pygame.draw.rect(self.screen, color, rect)

    def render(self):
        self.screen.fill((0, 0, 0))
        # Draw snake
        for pos in self.snake.positions:
            self.draw_cell(pos, (0, 255, 0))
        # Draw food
        self.draw_cell(self.food.position, (255, 0, 0))
        # Score
        score_surf = self.font.render(f"Score: {self.score}", True, (255, 255, 255))
        self.screen.blit(score_surf, (10, 10))
        pygame.display.flip()

    def render_game_over(self):
        self.screen.fill((0, 0, 0))
        over_surf = self.font.render("Game Over", True, (255, 0, 0))
        score_surf = self.font.render(f"Final Score: {self.score}", True, (255, 255, 255))
        restart_surf = self.font.render("Press R to Restart", True, (255, 255, 255))
        exit_surf = self.font.render("Press Q or ESC to Exit", True, (255, 255, 255))
        self.screen.blit(over_surf, (WINDOW_WIDTH // 2 - over_surf.get_width() // 2, WINDOW_HEIGHT // 3))
        self.screen.blit(score_surf, (WINDOW_WIDTH // 2 - score_surf.get_width() // 2, WINDOW_HEIGHT // 3 + 40))
        self.screen.blit(restart_surf, (WINDOW_WIDTH // 2 - restart_surf.get_width() // 2, WINDOW_HEIGHT // 3 + 80))
        self.screen.blit(exit_surf, (WINDOW_WIDTH // 2 - exit_surf.get_width() // 2, WINDOW_HEIGHT // 3 + 120))
        pygame.display.flip()

    def run(self):
        while True:
            if not self.game_over:
                self.handle_events()
                moved = self.snake.move()
                if not moved:
                    self.game_over = True
                else:
                    if self.snake.positions[0] == self.food.position:
                        self.snake.grow()
                        self.score += 1
                        self.fruits_eaten += 1
                        if self.fruits_eaten % SPEED_INCREASE_INTERVAL == 0 and self.fps > MIN_FPS:
                            self.fps -= SPEED_DECREMENT
                        self.food = Food(self.snake.positions)
                self.render()
            else:
                self.handle_game_over_events()
                self.render_game_over()
            self.clock.tick(self.fps)

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_UP:
                    self.snake.change_direction(UP)
                elif event.key == pygame.K_DOWN:
                    self.snake.change_direction(DOWN)
                elif event.key == pygame.K_LEFT:
                    self.snake.change_direction(LEFT)
                elif event.key == pygame.K_RIGHT:
                    self.snake.change_direction(RIGHT)

    def handle_game_over_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_r:
                    self.reset()
                elif event.key in (pygame.K_q, pygame.K_ESCAPE):
                    pygame.quit(); sys.exit()

if __name__ == "__main__":
    Game().run()
