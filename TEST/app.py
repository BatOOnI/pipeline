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
ENEMY_SPEED_INCREASE_INTERVAL = 5000  # ms
ENEMY_MAX_HP = 100
BULLET_DAMAGE = 50
SCORE_PER_ENEMY = 10

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

# Czcionki
text_font = pygame.font.SysFont(None, 36)
small_font = pygame.font.SysFont(None, 24)

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
        self.radius = BULLET_RADIUS
        self.speed = BULLET_SPEED
        self.angle = angle
        
    def update(self):
        self.x += math.cos(self.angle) * self.speed
        self.y += math.sin(self.angle) * self.speed
        
    def draw(self, screen):
        pygame.draw.circle(screen, GREEN, (int(self.x), int(self.y)), self.radius)
        
    def is_off_screen(self):
        return (self.x < -self.radius or self.x > SCREEN_WIDTH + self.radius or
                self.y < -self.radius or self.y > SCREEN_HEIGHT + self.radius)

# Klasa wroga
class Enemy:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.radius = ENEMY_RADIUS
        self.speed = ENEMY_STARTING_SPEED
        self.max_hp = ENEMY_MAX_HP
        self.hp = ENEMY_MAX_HP
        
    def update(self, player_x, player_y):
        # Oblicz wektor do gracza
        dx = player_x - self.x
        dy = player_y - self.y
        distance = math.sqrt(dx*dx + dy*dy)
        
        # Normalizacja wektora
        if distance > 0:
            dx /= distance
            dy /= distance
            
        # Ruch w stronę gracza
        self.x += dx * self.speed
        self.y += dy * self.speed
        
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

# Funkcja do generowania wrogów poza ekranem
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
    screen.fill(BLACK)
    
    game_over_text = text_font.render("GAME OVER", True, RED)
    score_text = text_font.render(f"Score: {score}", True, WHITE)
    restart_text = small_font.render("Press R to Restart", True, WHITE)
    
    screen.blit(game_over_text, (SCREEN_WIDTH//2 - game_over_text.get_width()//2, SCREEN_HEIGHT//2 - 60))
    screen.blit(score_text, (SCREEN_WIDTH//2 - score_text.get_width()//2, SCREEN_HEIGHT//2))
    screen.blit(restart_text, (SCREEN_WIDTH//2 - restart_text.get_width()//2, SCREEN_HEIGHT//2 + 60))

# Funkcja do rysowania pauzy
def draw_pause_screen(screen):
    overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 180))
    screen.blit(overlay, (0, 0))
    
    pause_text = text_font.render("PAUSED", True, WHITE)
    screen.blit(pause_text, (SCREEN_WIDTH//2 - pause_text.get_width()//2, SCREEN_HEIGHT//2))

# Główna funkcja gry
def main():
    player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    bullets = []
    enemies = []
    score = 0
    game_over = False
    paused = False
    last_enemy_spawn_time = pygame.time.get_ticks()
    last_speed_increase_time = pygame.time.get_ticks()
    enemy_speed = ENEMY_STARTING_SPEED
    
    # Główna pętla gry
    running = True
    while running:
        mouse_x, mouse_y = pygame.mouse.get_pos()
        
        # Obsługa zdarzeń
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    paused = not paused
                
                if game_over and event.key == pygame.K_r:
                    # Restart gry
                    player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
                    bullets = []
                    enemies = []
                    score = 0
                    game_over = False
                    paused = False
                    last_enemy_spawn_time = pygame.time.get_ticks()
                    last_speed_increase_time = pygame.time.get_ticks()
                    enemy_speed = ENEMY_STARTING_SPEED
            
            if event.type == pygame.MOUSEBUTTONDOWN and not game_over and not paused:
                if event.button == 1:  # Lewy przycisk myszy
                    # Strzał w kierunku kursora
                    player.update_angle(mouse_x, mouse_y)
                    bullets.append(Bullet(player.x, player.y, player.angle))
        
        if not paused and not game_over:
            # Ruch gracza
            keys = pygame.key.get_pressed()
            player.move(keys)
            
            # Obracanie gracza w stronę myszy
            player.update_angle(mouse_x, mouse_y)
            
            # Spawnowanie wrogów
            current_time = pygame.time.get_ticks()
            if current_time - last_enemy_spawn_time > 1000:  # Spawn co sekundę
                enemies.append(spawn_enemy())
                last_enemy_spawn_time = current_time
            
            # Zwiększanie prędkości wrogów co 5 sekund
            if current_time - last_speed_increase_time > ENEMY_SPEED_INCREASE_INTERVAL:
                enemy_speed *= 1.10  # Zwiększ o 10%
                last_speed_increase_time = current_time
            
            # Aktualizacja pocisków
            for bullet in bullets[:]:
                bullet.update()
                if bullet.is_off_screen():
                    bullets.remove(bullet)
                
            # Aktualizacja wrogów
            for enemy in enemies:
                enemy.update(player.x, player.y)
                
            # Sprawdzanie kolizji pocisków z wrogami
            for bullet in bullets[:]:
                for enemy in enemies[:]:
                    if check_collision(bullet.x, bullet.y, enemy.x, enemy.y, bullet.radius, enemy.radius):
                        enemy.hp -= BULLET_DAMAGE
                        if bullet in bullets:
                            bullets.remove(bullet)
                        
                        if enemy.hp <= 0:
                            enemies.remove(enemy)
                            score += SCORE_PER_ENEMY
                        break
            
            # Sprawdzanie kolizji wroga z graczem
            for enemy in enemies[:]:
                if check_collision(player.x, player.y, enemy.x, enemy.y, player.radius, enemy.radius):
                    game_over = True
        
        # Rysowanie
        screen.fill(WHITE)
        
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
            score_text = text_font.render(f"Score: {score}", True, BLACK)
            screen.blit(score_text, (10, 10))
            
            # Rysowanie prędkości wrogów
            speed_text = small_font.render(f"Enemy Speed: {enemy_speed:.1f}", True, BLACK)
            screen.blit(speed_text, (SCREEN_WIDTH - speed_text.get_width() - 10, 10))
        
        if game_over:
            draw_game_over(screen, score)
        
        if paused and not game_over:
            draw_pause_screen(screen)
        
        pygame.display.flip()
        clock.tick(60)
    
    pygame.quit()
    sys.exit()

# Uruchomienie gry
if __name__ == "__main__":
    main()