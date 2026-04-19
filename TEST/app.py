import pygame
import random
import math

# --- Konfiguracja ---
WIDTH, HEIGHT = 900, 600
FPS = 60

PLAYER_RADIUS = 22
PLAYER_SPEED = 260
BULLET_SPEED = 520

ENEMY_HP = 100
BULLET_DAMAGE = 50

SCORE_KILL = 10

ENEMY_BASE_SPEED = 70
ENEMY_SPEED_INCREASE = 0.10  # +10%
ENEMY_SPEED_INTERVAL = 5.0   # seconds

BG_COLOR = (18, 18, 24)
WHITE = (240, 240, 240)
RED = (220, 60, 60)
GREEN = (60, 220, 90)
BLACK = (0, 0, 0)


def clamp(x, a, b):
    return a if x < a else b if x > b else x


def vec_from_angle(angle_rad):
    return math.cos(angle_rad), math.sin(angle_rad)


def angle_to_mouse(px, py, mx, my):
    return math.atan2(my - py, mx - px)


def spawn_enemy_outside_screen():
    # Spawn losowo poza widocznym obszarem: wybieramy stronę i losujemy pozycję poza ekranem.
    side = random.choice(["left", "right", "top", "bottom"])
    margin = 80
    if side == "left":
        x = -margin - random.randint(0, 200)
        y = random.randint(0, HEIGHT)
    elif side == "right":
        x = WIDTH + margin + random.randint(0, 200)
        y = random.randint(0, HEIGHT)
    elif side == "top":
        x = random.randint(0, WIDTH)
        y = -margin - random.randint(0, 200)
    else:  # bottom
        x = random.randint(0, WIDTH)
        y = HEIGHT + margin + random.randint(0, 200)
    return [float(x), float(y), ENEMY_HP]


def draw_text_center(surf, font, text, y, color=WHITE):
    img = font.render(text, True, color)
    rect = img.get_rect(center=(WIDTH // 2, y))
    surf.blit(img, rect)


def main():
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Space Shooter")
    clock = pygame.time.Clock()

    # Inicjalizacja gracza
    player = Player(WIDTH // 2, HEIGHT // 2)

    # Lista obiektów
    bullets = []
    enemies = []
    particles = []

    # Zmienne do spawnowania wrogów
    last_enemy_spawn_time = time.time()
    enemy_spawn_interval = 1.0  # sekundy

    # Główna pętla gry
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0  # Czas w sekundach

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

        # Aktualizacja gracza
        player.update(dt)

        # Spawnowanie wrogów
        current_time = time.time()
        if current_time - last_enemy_spawn_time > enemy_spawn_interval:
            enemies.append(spawn_enemy_outside_screen())
            last_enemy_spawn_time = current_time

        # Aktualizacja pocisków
        for bullet in bullets[:]:
            bullet.update(dt)
            if bullet.x < 0 or bullet.x > WIDTH or bullet.y < 0 or bullet.y > HEIGHT:
                bullets.remove(bullet)

        # Aktualizacja wrogów
        for enemy in enemies[:]:
            enemy.update(dt)
            if enemy.x < -50 or enemy.x > WIDTH + 50 or enemy.y < -50 or enemy.y > HEIGHT + 50:
                enemies.remove(enemy)

        # Renderowanie
        screen.fill(BG_COLOR)
        player.draw(screen)
        for bullet in bullets:
            bullet.draw(screen)
        for enemy in enemies:
            enemy.draw(screen)
        for particle in particles:
            particle.draw(screen)

        pygame.display.flip()

    pygame.quit()
    pygame.quit()


if __name__ == "__main__":
    main()
