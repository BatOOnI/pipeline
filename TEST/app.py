import sys
import random
import pygame

# Constants
WIDTH, HEIGHT = 800, 600
CELL_SIZE = 20
GRID_WIDTH = WIDTH // CELL_SIZE
GRID_HEIGHT = HEIGHT // CELL_SIZE
FPS_START = 10
SPEED_INCREASE_INTERVAL = 5

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (200, 0, 0)
GREEN = (0, 180, 0)
BLUE = (0, 0, 200)
YELLOW = (200, 200, 0)

pygame.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Snake")
clock = pygame.time.Clock()
font = pygame.font.SysFont(None, 36)

# Helper functions

def draw_text(text, color, pos):
    img = font.render(text, True, color)
    screen.blit(img, pos)

def random_food_position(snake):
    while True:
        pos = (random.randint(0, GRID_WIDTH - 1), random.randint(0, GRID_HEIGHT - 1))
        if pos not in snake:
            return pos

# Game loop

def main():
    # Initial state
    snake = [(GRID_WIDTH // 2, GRID_HEIGHT // 2)]
    direction = (0, -1)  # moving up initially
    next_direction = direction
    food = random_food_position(snake)
    score = 0
    foods_eaten = 0
    fps = FPS_START
    game_over = False

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            elif event.type == pygame.KEYDOWN and not game_over:
                if event.key == pygame.K_UP and direction != (0, 1):
                    next_direction = (0, -1)
                elif event.key == pygame.K_DOWN and direction != (0, -1):
                    next_direction = (0, 1)
                elif event.key == pygame.K_LEFT and direction != (1, 0):
                    next_direction = (-1, 0)
                elif event.key == pygame.K_RIGHT and direction != (-1, 0):
                    next_direction = (1, 0)
            elif event.type == pygame.KEYDOWN and game_over:
                if event.key == pygame.K_r:
                    main()  # restart
                elif event.key == pygame.K_q or event.key == pygame.K_ESCAPE:
                    pygame.quit()
                    sys.exit()

        if not game_over:
            direction = next_direction
            new_head = ((snake[0][0] + direction[0]) % GRID_WIDTH,
                        (snake[0][1] + direction[1]) % GRID_HEIGHT)
            if new_head in snake:
                game_over = True
            else:
                snake.insert(0, new_head)
                if new_head == food:
                    score += 10
                    foods_eaten += 1
                    food = random_food_position(snake)
                    if foods_eaten % SPEED_INCREASE_INTERVAL == 0:
                        fps += 1
                else:
                    snake.pop()

        # Drawing
        screen.fill(BLACK)
        # Draw food
        pygame.draw.rect(screen, RED,
                         (food[0]*CELL_SIZE, food[1]*CELL_SIZE, CELL_SIZE, CELL_SIZE))
        # Draw snake
        for segment in snake:
            pygame.draw.rect(screen, GREEN,
                             (segment[0]*CELL_SIZE, segment[1]*CELL_SIZE, CELL_SIZE, CELL_SIZE))
        # Score
        draw_text(f"Score: {score}", WHITE, (10, 10))

        if game_over:
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            screen.blit(overlay, (0, 0))
            draw_text("Game Over", YELLOW, (WIDTH//2 - 80, HEIGHT//2 - 60))
            draw_text(f"Final Score: {score}", WHITE, (WIDTH//2 - 100, HEIGHT//2 - 20))
            draw_text("Press R to Restart or Q to Quit", WHITE, (WIDTH//2 - 180, HEIGHT//2 + 20))

        pygame.display.flip()
        clock.tick(fps)

if __name__ == "__main__":
    main()
