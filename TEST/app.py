import pygame
import sys
import math
import random

# Inicjalizacja Pygame
pygame.init()

# Stałe
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
PLAYER_RADIUS = 20
BULLET_RADIUS = 5
ENEMY_RADIUS = 15
PLAYER_SPEED = 5
BULLET_SPEED = 10
ENEMY_SPAWN_DISTANCE = 100
ENEMY_STARTING_SPEED = 2.0
ENEMY_HP = 100
BULLET_DAMAGE = 50
ENEMY_REWARD = 10
ENEMY_SPEED_INCREASE_INTERVAL = 5000  # ms

# Kolory
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
GRAY = (128, 128, 128)

# Ustawienia ekranu
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption("Top-Down Shooter")
clock = pygame.time.Clock()

# Klasa gracza
class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.radius = PLAYER_RADIUS
        self.speed = PLAYER_SPEED
        self.angle = 0
        
    def move(self, keys):
        if keys[pygame.K_w]:
            self.y -= self.speed
        if keys[pygame.K_s]:
            self.y += self.speed
        if keys[pygame.K_a]:
            self.x -= self.speed
        if keys[pygame.K_d]:
            self.x += self.speed
        
        # Ograniczenie ruchu do ekranu
        self.x = max(self.radius, min(SCREEN_WIDTH - self.radius, self.x))
        self.y = max(self.radius, min(SCREEN_HEIGHT - self.radius, self.y))
        
    def update_angle(self, mouse_x, mouse_y):
        dx = mouse_x - self.x
        dy = mouse_y - self.y
        self.angle = math.atan2(dy, dx)
        
    def draw(self, screen):
        # Rysowanie gracza jako okręgu
        pygame.draw.circle(screen, BLUE, (int(self.x), int(self.y)), self.radius)
        
        # Rysowanie lufy (krótka linia na krawędzi koła)
        end_x = self.x + math.cos(self.angle) * self.radius
        end_y = self.y + math.sin(self.angle) * self.radius
        pygame.draw.line(screen, BLACK, (self.x, self.y), (end_x, end_y), 4)

# Klasa pocisku
class Bullet:
    def __init__(self, x, y, angle):
        self.x = x
        self.y = y
        self.angle = angle
        self.speed = BULLET_SPEED
        self.radius = BULLET_RADIUS
        
    def update(self):
        self.x += math.cos(self.angle) * self.speed
        self.y += math.sin(self.angle) * self.speed
        
    def draw(self, screen):
        pygame.draw.circle(screen, GREEN, (int(self.x), int(self.y)), self.radius)
        
    def is_off_screen(self):
        return (self.x < 0 or self.x > SCREEN_WIDTH or 
                self.y < 0 or self.y > SCREEN_HEIGHT)

# Klasa wroga
class Enemy:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.radius = ENEMY_RADIUS
        self.hp = ENEMY_HP
        self.max_hp = ENEMY_HP
        
    def update(self, player_x, player_y, speed_multiplier):
        # Oblicz wektor do gracza
        dx = player_x - self.x
        dy = player_y - self.y
        distance = math.sqrt(dx*dx + dy*dy)
        
        # Normalizacja wektora
        if distance > 0:
            dx /= distance
            dy /= distance
            
        # Ruch w stronę gracza z mnożnikiem prędkości
        self.x += dx * ENEMY_STARTING_SPEED * speed_multiplier
        self.y += dy * ENEMY_STARTING_SPEED * speed_multiplier
        
    def draw(self, screen):
        # Rysowanie wroga jako okręgu
        pygame.draw.circle(screen, RED, (int(self.x), int(self.y)), self.radius)
        
        # Rysowanie paska życia
        bar_width = self.radius * 2
        bar_height = 5
        bar_x = self.x - bar_width / 2
        bar_y = self.y - self.radius - 10
        
        # Tło paska życia
        pygame.draw.rect(screen, BLACK, (bar_x, bar_y, bar_width, bar_height))
        
        # Żyje pasek
        hp_ratio = self.hp / self.max_hp
        pygame.draw.rect(screen, RED, (bar_x, bar_y, bar_width * hp_ratio, bar_height))

# Funkcja do generowania losowego punktu poza ekranem
def spawn_enemy():
    side = random.choice(['top', 'bottom', 'left', 'right'])
    if side == 'top':
        x = random.randint(0, SCREEN_WIDTH)
        y = -ENEMY_SPAWN_DISTANCE
    elif side == 'bottom':
        x = random.randint(0, SCREEN_WIDTH)
        y = SCREEN_HEIGHT + ENEMY_SPAWN_DISTANCE
    elif side == 'left':
        x = -ENEMY_SPAWN_DISTANCE
        y = random.randint(0, SCREEN_HEIGHT)
    else:  # right
        x = SCREEN_WIDTH + ENEMY_SPAWN_DISTANCE
        y = random.randint(0, SCREEN_HEIGHT)
    return Enemy(x, y)

# Funkcja do sprawdzania kolizji
def check_collision(obj1_x, obj1_y, obj2_x, obj2_y, radius1, radius2):
    distance = math.sqrt((obj1_x - obj2_x)**2 + (obj1_y - obj2_y)**2)
    return distance < (radius1 + radius2)

# Funkcja do rysowania ekranu game over
def draw_game_over(screen, score):
    font_large = pygame.font.SysFont(None, 72)
    font_small = pygame.font.SysFont(None, 36)
    
    # Tekst Game Over
    text = font_large.render("GAME OVER", True, RED)
    screen.blit(text, (SCREEN_WIDTH//2 - text.get_width()//2, SCREEN_HEIGHT//2 - 50))
    
    # Wynik
    score_text = font_small.render(f"Score: {score}", True, WHITE)
    screen.blit(score_text, (SCREEN_WIDTH//2 - score_text.get_width()//2, SCREEN_HEIGHT//2 + 20))
    
    # Instrukcje
    restart_text = font_small.render("Press R to Restart", True, WHITE)
    screen.blit(restart_text, (SCREEN_WIDTH//2 - restart_text.get_width()//2, SCREEN_HEIGHT//2 + 70))
    
    esc_text = font_small.render("Press ESC to Resume", True, WHITE)
    screen.blit(esc_text, (SCREEN_WIDTH//2 - esc_text.get_width()//2, SCREEN_HEIGHT//2 + 110))

# Funkcja do rysowania pauzy
def draw_pause_screen(screen):
    font_large = pygame.font.SysFont(None, 72)
    text = font_large.render("PAUSED", True, WHITE)
    screen.blit(text, (SCREEN_WIDTH//2 - text.get_width()//2, SCREEN_HEIGHT//2))

# Funkcja do rysowania ekranu startowego
def draw_start_screen(screen):
    font_large = pygame.font.SysFont(None, 72)
    font_small = pygame.font.SysFont(None, 36)
    
    text = font_large.render("TOP-DOWN SHOOTER", True, WHITE)
    screen.blit(text, (SCREEN_WIDTH//2 - text.get_width()//2, SCREEN_HEIGHT//2 - 100))
    
    start_text = font_small.render("Press SPACE to Start", True, WHITE)
    screen.blit(start_text, (SCREEN_WIDTH//2 - start_text.get_width()//2, SCREEN_HEIGHT//2))

# Główna funkcja gry
def main():
    player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    bullets = []
    enemies = []
    score = 0
    game_over = False
    paused = False
    last_enemy_spawn = pygame.time.get_ticks()
    last_speed_increase = pygame.time.get_ticks()
    speed_multiplier = 1.0
    
    # Główna pętla gry
    running = True
    while running:
        mouse_x, mouse_y = pygame.mouse.get_pos()
        
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    paused = not paused
                elif event.key == pygame.K_r and game_over:
                    # Restart gry
                    player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
                    bullets = []
                    enemies = []
                    score = 0
                    game_over = False
                    paused = False
                    last_enemy_spawn = pygame.time.get_ticks()
                    last_speed_increase = pygame.time.get_ticks()
                    speed_multiplier = 1.0
                elif event.key == pygame.K_SPACE and not game_over:
                    # Rozpocznij grę
                    pass
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and not game_over and not paused:
                # Strzał
                bullet_x = player.x + math.cos(player.angle) * player.radius
                bullet_y = player.y + math.sin(player.angle) * player.radius
                bullets.append(Bullet(bullet_x, bullet_y, player.angle))
        
        if not game_over and not paused:
            # Ruch gracza
            keys = pygame.key.get_pressed()
            player.move(keys)
            
            # Obracanie gracza w stronę myszy
            player.update_angle(mouse_x, mouse_y)
            
            # Spawnowanie wrogów
            current_time = pygame.time.get_ticks()
            if current_time - last_enemy_spawn > 1000:  # Spawn co sekundę
                enemies.append(spawn_enemy())
                last_enemy_spawn = current_time
            
            # Zwiększanie prędkości wrogów co 5 sekund
            if current_time - last_speed_increase > ENEMY_SPEED_INCREASE_INTERVAL:
                speed_multiplier *= 1.10  # Zwiększ o 10%
                last_speed_increase = current_time
            
            # Aktualizacja pocisków
            for bullet in bullets[:]:
                bullet.update()
                if bullet.is_off_screen():
                    bullets.remove(bullet)
                
            # Aktualizacja wrogów
            for enemy in enemies:
                enemy.update(player.x, player.y, speed_multiplier)
                
            # Kolizje pocisków z wrogami
            for bullet in bullets[:]:
                for enemy in enemies[:]:
                    if check_collision(bullet.x, bullet.y, enemy.x, enemy.y, BULLET_RADIUS, ENEMY_RADIUS):
                        enemy.hp -= BULLET_DAMAGE
                        if enemy.hp <= 0:
                            enemies.remove(enemy)
                            score += ENEMY_REWARD
                        try:
                            bullets.remove(bullet)
                        except ValueError:
                            pass
                        break
            
            # Kolizje wrogów z graczem
            for enemy in enemies[:]:
                if check_collision(player.x, player.y, enemy.x, enemy.y, PLAYER_RADIUS, ENEMY_RADIUS):
                    game_over = True
        
        # Rysowanie
        screen.fill(BLACK)
        
        if not game_over:
            # Rysowanie gracza
            player.draw(screen)
            
            # Rysowanie pocisków
            for bullet in bullets:
                bullet.draw(screen)
            
            # Rysowanie wrogów
            for enemy in enemies:
                enemy.draw(screen)
            
            # Rysowanie punktów
            font = pygame.font.SysFont(None, 36)
            score_text = font.render(f"Score: {score}", True, WHITE)
            screen.blit(score_text, (10, 10))
            
            # Rysowanie prędkości wrogów
            speed_text = font.render(f"Enemy Speed: {speed_multiplier:.2f}x", True, WHITE)
            screen.blit(speed_text, (10, 50))
        else:
            draw_game_over(screen, score)
        
        if paused and not game_over:
            draw_pause_screen(screen)
        
        pygame.display.flip()
        pygame.time.Clock().tick(60)
    
    pygame.quit()

if __name__ == "__main__":
    main()