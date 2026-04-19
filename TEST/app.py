import pygame
import sys

# Initialize pygame
pygame.init()

# Set up display
screen = pygame.display.set_mode((800, 600))
pygame.display.set_caption('Game with Pause')

# Game clock
clock = pygame.time.Clock()

# Game state
paused = False

# Main game loop
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                paused = not paused

    # Clear screen
    screen.fill((0, 0, 0))

    # Draw game elements
    if not paused:
        # Game logic here
        pass
    else:
        # Pause screen
        font = pygame.font.Font(None, 74)
        text = font.render('PAUSED', True, (255, 255, 255))
        screen.blit(text, (300, 250))

    # Update display
    pygame.display.flip()
    clock.tick(60)

pygame.quit()
sys.exit()# Add pause functionality
paused = False

def toggle_pause():
    global paused
    paused = not paused

# In your main game loop, add:
# if event.type == pygame.KEYDOWN:
#     if event.key == pygame.K_ESCAPE:
#         toggle_pause()

# Then in your game loop, check for paused state and pause rendering/updating
