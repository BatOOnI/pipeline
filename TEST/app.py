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
    pygame.display.set_caption("Top-down Shooter")
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    clock = pygame.time.Clock()

    font = pygame.font.SysFont(None, 28)
    big_font = pygame.font.SysFont(None, 64)

    # --- Stan gry ---
    elapsed = 0
    player_x = WIDTH / 2
    player_y = HEIGHT / 2
    player_angle = 0
    enemies = []
    bullets = []
    score = 0
    paused = False
    game_over = False
    last_enemy_spawn = 0

    while True:
        dt = clock.tick(60) / 1000.0
        elapsed += dt

        # --- Obsługa zdarzeń ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    paused = not paused

        if paused:
            # --- Rysowanie pauzy ---
            screen.fill(BLACK)
            draw_text_center(screen, big_font, "PAUZA", HEIGHT // 2 - 50, WHITE)
            draw_text_center(screen, font, "Naciśnij ESC, aby kontynuować", HEIGHT // 2 + 50, WHITE)
            pygame.display.flip()
            continue

        # --- Rysowanie ---
        screen.fill(BLACK)
        # ... rest of drawing code would go here ...
    last_enemy_spawn = 0
    enemy_speed_increase_timer = 0
    # --- Stan gry ---
    player_x = WIDTH / 2
    player_y = HEIGHT / 2
    player_angle = 0
    enemies = []
    bullets = []
    score = 0
    paused = False
    
    running = True
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        elapsed += dt

        # --- Input ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    paused = not paused
                if game_over and event.key == pygame.K_r:
                    # Restart
                    game_over = False
                    bullets.clear()
                    enemies.clear()
                    score = 0
                    enemy_speed = ENEMY_BASE_SPEED
                    next_speed_increase_time = ENEMY_SPEED_INTERVAL
                    elapsed = 0.0
                    player_x = WIDTH / 2
                    player_y = HEIGHT / 2
                    for _ in range(6):
                        enemies.append(spawn_enemy_outside_screen())

            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and not game_over:
                    shooting = True

            if event.type == pygame.MOUSEBUTTONUP:
                if event.button == 1:
                    shooting = False

        # --- Update ---
        if not game_over:
            mx, my = pygame.mouse.get_pos()
            player_angle = angle_to_mouse(player_x, player_y, mx, my)

            keys = pygame.key.get_pressed()
            dx = 0.0
            dy = 0.0
            if keys[pygame.K_a]:
                dx -= 1.0
            if keys[pygame.K_d]:
                dx += 1.0
            if keys[pygame.K_w]:
                dy -= 1.0
            if keys[pygame.K_s]:
                dy += 1.0

            # Normalize movement
            mag = math.hypot(dx, dy)
            if mag > 0:
                dx /= mag
                dy /= mag

            player_x += dx * PLAYER_SPEED * dt
            player_y += dy * PLAYER_SPEED * dt

            # Keep player within bounds (soft clamp)
            player_x = clamp(player_x, PLAYER_RADIUS, WIDTH - PLAYER_RADIUS)
            player_y = clamp(player_y, PLAYER_RADIUS, HEIGHT - PLAYER_RADIUS)

            # One click = one shot
            if shooting:
                # Fire once per click: we set shooting True on down, but it stays True until up.
                # To ensure exactly one shot per click, we immediately set shooting False after spawning.
                shooting = False
                vx, vy = vec_from_angle(player_angle)
                # Spawn bullet at lufa end
                lufa_len = 18
                bx = player_x + vx * (PLAYER_RADIUS + 2)
                by = player_y + vy * (PLAYER_RADIUS + 2)
                bullets.append([bx, by, vx * BULLET_SPEED, vy * BULLET_SPEED])

            # Enemy speed increases every 5 seconds by 10%
            if elapsed >= next_speed_increase_time:
                # Increase possibly multiple times if lag
                while elapsed >= next_speed_increase_time:
                    enemy_speed *= (1.0 + ENEMY_SPEED_INCREASE)
                    next_speed_increase_time += ENEMY_SPEED_INTERVAL

            # Update bullets
            for b in bullets:
                b[0] += b[2] * dt
                b[1] += b[3] * dt

            # Remove bullets off-screen
            bullets = [b for b in bullets if -50 <= b[0] <= WIDTH + 50 and -50 <= b[1] <= HEIGHT + 50]

            # Update enemies
            for e in enemies:
                ex, ey, hp = e
                ang = angle_to_mouse(ex, ey, player_x, player_y)
                vx, vy = vec_from_angle(ang)
                ex += vx * enemy_speed * dt
                ey += vy * enemy_speed * dt
                e[0], e[1] = ex, ey

            # Bullet-enemy collisions
            # Enemy radius for collision and drawing
            ENEMY_RADIUS = 20
            for b in bullets[:]:
                bx, by = b[0], b[1]
                hit = False
                for e in enemies[:]:
                    ex, ey, hp = e
                    if (bx - ex) ** 2 + (by - ey) ** 2 <= (ENEMY_RADIUS) ** 2:
                        hp -= BULLET_DAMAGE
                        e[2] = hp
                        hit = True
                        if hp <= 0:
                            enemies.remove(e)
                            score += SCORE_KILL
                            # Respawn to keep pressure
                            enemies.append(spawn_enemy_outside_screen())
                        break
                if hit:
                    bullets.remove(b)

            # Enemy-player collision => game over
            for e in enemies:
                ex, ey, hp = e
                if (ex - player_x) ** 2 + (ey - player_y) ** 2 <= (PLAYER_RADIUS + ENEMY_RADIUS - 2) ** 2:
                    game_over = True
                    break

        # --- Draw ---
        screen.fill(BG_COLOR)

        # Draw bullets
        for b in bullets:
            pygame.draw.circle(screen, (240, 240, 120), (int(b[0]), int(b[1])), 4)

        # Draw enemies with HP bar
        ENEMY_RADIUS = 20
        for e in enemies:
            ex, ey, hp = e
            pygame.draw.circle(screen, (80, 80, 220), (int(ex), int(ey)), ENEMY_RADIUS)

            # Red HP bar above enemy
            bar_w = 44
            bar_h = 7
            x0 = int(ex) - bar_w // 2
            y0 = int(ey) - ENEMY_RADIUS - 14
            pygame.draw.rect(screen, (120, 0, 0), (x0, y0, bar_w, bar_h))
            hp_ratio = max(0.0, min(1.0, hp / ENEMY_HP))
            pygame.draw.rect(screen, RED, (x0, y0, int(bar_w * hp_ratio), bar_h))

        # Draw player (circle) and short gun line on edge
        pygame.draw.circle(screen, (70, 220, 220), (int(player_x), int(player_y)), PLAYER_RADIUS)
        # Lufa: krótka linia na krawędzi koła
        gun_len = 18
        gx1 = player_x + math.cos(player_angle) * (PLAYER_RADIUS - 2)
        gy1 = player_y + math.sin(player_angle) * (PLAYER_RADIUS - 2)
        gx2 = player_x + math.cos(player_angle) * (PLAYER_RADIUS - 2 + gun_len)
        gy2 = player_y + math.sin(player_angle) * (PLAYER_RADIUS - 2 + gun_len)
        pygame.draw.line(screen, (240, 240, 240), (int(gx1), int(gy1)), (int(gx2), int(gy2)), 4)

        # UI: SCORE left top
        score_img = font.render(f"SCORE: {score}", True, WHITE)
        screen.blit(score_img, (12, 10))

        # UI: enemy speed on opposite side (right top)
        speed_img = font.render(f"ENEMY SPEED: {enemy_speed:.1f}", True, WHITE)
        screen.blit(speed_img, (WIDTH - speed_img.get_width() - 12, 10))

        if game_over:
            # Game over screen
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 160))
            screen.blit(overlay, (0, 0))
            draw_text_center(screen, big_font, "GAME OVER", HEIGHT // 2 - 40, (255, 255, 255))
            draw_text_center(screen, font, f"SCORE: {score}", HEIGHT // 2 + 10, (255, 255, 255))
            draw_text_center(screen, font, "Naciśnij R aby zagrać ponownie", HEIGHT // 2 + 40, (220, 220, 220))

        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
