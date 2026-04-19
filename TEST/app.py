import pygame
import sys

# Initialize pygame
pygame.init()

# Screen dimensions
WIDTH, HEIGHT = 800, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Game with Pause")

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)

# Game variables
clock = pygame.time.Clock()
paused = False

# Font
font = pygame.font.Font(None, 36)

# Main game loop
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                paused = not paused

    if not paused:
        # Game logic here
        pass

    # Drawing
    screen.fill(WHITE)
    
    if paused:
        pause_text = font.render("PAUSED - Press ESC to continue", True, BLACK)
        screen.blit(pause_text, (WIDTH//2 - pause_text.get_width()//2, HEIGHT//2))
    else:
        # Draw game elements here
        pygame.draw.rect(screen, RED, (100, 100, 50, 50))
        
    pygame.display.flip()
    clock.tick(60)

pygame.quit()
sys.exit()