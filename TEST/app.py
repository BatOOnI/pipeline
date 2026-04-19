import pygame
import random
import math

# Inicjalizacja pygame
pygame.init()

# Stałe
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
PLAYER_SIZE = 30
ENEMY_SIZE = 20
BULLET_SIZE = 5
PLAYER_SPEED = 5
BULLET_SPEED = 10
ENEMY_SPAWN_DISTANCE = 100

# Kolory
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)

# Ustawienia ekranu
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption("Top-Down Shooter")
clock = pygame.time.Clock()

# Klasa gracza
class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.size = PLAYER_SIZE
        self.speed = PLAYER_SPEED
        
    def move(self, dx, dy):
        self.x += dx * self.speed
        self.y += dy * self.speed
        
        # Ograniczenia do ekranu
        self.x = max(0, min(SCREEN_WIDTH - self.size, self.x))
        self.y = max(0, min(SCREEN_HEIGHT - self.size, self.y))
        
    def draw(self, screen):
        pygame.draw.rect(screen, GREEN, (self.x, self.y, self.size, self.size))

# Klasa wroga
class Enemy:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.size = ENEMY_SIZE
        self.speed = random.uniform(1.0, 3.0)
        
    def move_towards_player(self, player_x, player_y):
        # Oblicz kierunek do gracza
        dx = player_x - self.x
        dy = player_y - self.y
        distance = max(1, math.sqrt(dx*dx + dy*dy))
        
        # Normalizacja wektora i przesunięcie
        dx /= distance
        dy /= distance
        
        self.x += dx * self.speed
        self.y += dy * self.speed
        
    def draw(self, screen):
        pygame.draw.rect(screen, RED, (self.x, self.y, self.size, self.size))

# Klasa pocisku
class Bullet:
    def __init__(self, x, y, target_x, target_y):
        self.x = x
        self.y = y
        self.size = BULLET_SIZE
        
        # Oblicz kierunek
        dx = target_x - x
        dy = target_y - y
        distance = max(1, math.sqrt(dx*dx + dy*dy))
        
        self.dx = (dx / distance) * BULLET_SPEED
        self.dy = (dy / distance) * BULLET_SPEED
        
    def update(self):
        self.x += self.dx
        self.y += self.dy
        
    def draw(self, screen):
        pygame.draw.circle(screen, BLUE, (int(self.x), int(self.y)), self.size)

# Inicjalizacja gracza
player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)

# Lista wrogów i pocisków
enemies = []
bullets = []

# Zegar spawnowania wrogów
enemy_spawn_timer = 0
enemy_spawn_delay = 1000  # ms

# Główna pętla gry
running = True
while running:
    current_time = pygame.time.get_ticks()
    
    # Obsługa zdarzeń
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.MOUSEBUTTONDOWN:
            # Strzelanie po kliknięciu myszy
            mouse_x, mouse_y = pygame.mouse.get_pos()
            bullets.append(Bullet(player.x + PLAYER_SIZE//2, player.y + PLAYER_SIZE//2, mouse_x, mouse_y))
    
    # Obsługa klawiszy
    keys = pygame.key.get_pressed()
    dx, dy = 0, 0
    if keys[pygame.K_w] or keys[pygame.K_UP]:
        dy -= 1
    if keys[pygame.K_s] or keys[pygame.K_DOWN]:
        dy += 1
    if keys[pygame.K_a] or keys[pygame.K_LEFT]:
        dx -= 1
    if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
        dx += 1
    
    # Ruch gracza
    player.move(dx, dy)
    
    # Spawnowanie wrogów
    if current_time - enemy_spawn_timer > enemy_spawn_delay:
        # Wybierz losową stronę ekranu
        side = random.choice(['top', 'bottom', 'left', 'right'])
        
        if side == 'top':
            x = random.randint(-ENEMY_SIZE, SCREEN_WIDTH)
            y = -ENEMY_SIZE - ENEMY_SPAWN_DISTANCE
        elif side == 'bottom':
            x = random.randint(-ENEMY_SIZE, SCREEN_WIDTH)
            y = SCREEN_HEIGHT + ENEMY_SIZE + ENEMY_SPAWN_DISTANCE
        elif side == 'left':
            x = -ENEMY_SIZE - ENEMY_SPAWN_DISTANCE
            y = random.randint(-ENEMY_SIZE, SCREEN_HEIGHT)
        else:  # right
            x = SCREEN_WIDTH + ENEMY_SIZE + ENEMY_SPAWN_DISTANCE
            y = random.randint(-ENEMY_SIZE, SCREEN_HEIGHT)
            
        enemies.append(Enemy(x, y))
        enemy_spawn_timer = current_time
    
    # Aktualizacja wrogów
    for enemy in enemies[:]:
        enemy.move_towards_player(player.x + PLAYER_SIZE//2, player.y + PLAYER_SIZE//2)
        
        # Usuń wroga jeśli wyjdzie poza ekran
        if (enemy.x < -ENEMY_SIZE or enemy.x > SCREEN_WIDTH or
            enemy.y < -ENEMY_SIZE or enemy.y > SCREEN_HEIGHT):
            enemies.remove(enemy)
    
    # Aktualizacja pocisków
    for bullet in bullets[:]:
        bullet.update()
        
        # Usuń pocisk jeśli wyjdzie poza ekran
        if (bullet.x < 0 or bullet.x > SCREEN_WIDTH or
            bullet.y < 0 or bullet.y > SCREEN_HEIGHT):
            bullets.remove(bullet)
    
    # Sprawdzenie kolizji pocisków z wrogami
    for bullet in bullets[:]:
        for enemy in enemies[:]:
            distance = math.sqrt((bullet.x - (enemy.x + ENEMY_SIZE//2))**2 + 
                                (bullet.y - (enemy.y + ENEMY_SIZE//2))**2)
            if distance < (BULLET_SIZE + ENEMY_SIZE//2):
                if bullet in bullets:
                    bullets.remove(bullet)
                if enemy in enemies:
                    enemies.remove(enemy)
                break
    
    # Rysowanie
    screen.fill(WHITE)
    
    # Rysowanie gracza
    player.draw(screen)
    
    # Rysowanie wrogów
    for enemy in enemies:
        enemy.draw(screen)
    
    # Rysowanie pocisków
    for bullet in bullets:
        bullet.draw(screen)
    
    pygame.display.flip()
    clock.tick(60)

pygame.quit()