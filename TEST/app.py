import pygame
import random
import math

# Inicjalizacja pygame
pygame.init()

# Stałe
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
PLAYER_SIZE = 20
ENEMY_SIZE = 15
BULLET_SIZE = 5
PLAYER_SPEED = 5
BULLET_SPEED = 10
ENEMY_SPAWN_RATE = 60  # co ile klatek spawnuje sie wrog

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
        self.speed = random.uniform(0.5, 2.0)
        
    def move_towards_player(self, player_x, player_y):
        # Oblicz wektor kierunku do gracza
        dx = player_x - self.x
        dy = player_y - self.y
        distance = max(1, math.sqrt(dx*dx + dy*dy))
        
        # Normalizuj wektor i przesuń wroga
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
        
        # Oblicz wektor kierunku do celu
        dx = target_x - x
        dy = target_y - y
        distance = max(1, math.sqrt(dx*dx + dy*dy))
        
        # Normalizuj wektor i ustaw prędkość
        self.dx = dx / distance * BULLET_SPEED
        self.dy = dy / distance * BULLET_SPEED
        
    def update(self):
        self.x += self.dx
        self.y += self.dy
        
    def draw(self, screen):
        pygame.draw.circle(screen, BLUE, (int(self.x), int(self.y)), self.size)

# Inicjalizacja gracza
player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)

# Listy obiektów
enemies = []
bullets = []

# Zmienna do śledzenia spawnowania wrogów
spawn_timer = 0

# Główna pętla gry
running = True
while running:
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
    if keys[pygame.K_w]:
        dy -= 1
    if keys[pygame.K_s]:
        dy += 1
    if keys[pygame.K_a]:
        dx -= 1
    if keys[pygame.K_d]:
        dx += 1
    
    player.move(dx, dy)
    
    # Spawnowanie wrogów
    spawn_timer += 1
    if spawn_timer >= ENEMY_SPAWN_RATE:
        spawn_timer = 0
        
        # Wybierz stronę, z której się pojawi wrog
        side = random.choice(['top', 'bottom', 'left', 'right'])
        
        if side == 'top':
            x = random.randint(-ENEMY_SIZE*2, SCREEN_WIDTH + ENEMY_SIZE*2)
            y = -ENEMY_SIZE
        elif side == 'bottom':
            x = random.randint(-ENEMY_SIZE*2, SCREEN_WIDTH + ENEMY_SIZE*2)
            y = SCREEN_HEIGHT
        elif side == 'left':
            x = -ENEMY_SIZE
            y = random.randint(-ENEMY_SIZE*2, SCREEN_HEIGHT + ENEMY_SIZE*2)
        else:  # right
            x = SCREEN_WIDTH
            y = random.randint(-ENEMY_SIZE*2, SCREEN_HEIGHT + ENEMY_SIZE*2)
            
        enemies.append(Enemy(x, y))
    
    # Aktualizacja wrogów
    for enemy in enemies[:]:
        enemy.move_towards_player(player.x + PLAYER_SIZE//2, player.y + PLAYER_SIZE//2)
        
        # Usuń wroga jeśli wyjdzie poza ekran
        if (enemy.x < -ENEMY_SIZE*3 or enemy.x > SCREEN_WIDTH + ENEMY_SIZE*3 or
            enemy.y < -ENEMY_SIZE*3 or enemy.y > SCREEN_HEIGHT + ENEMY_SIZE*3):
            enemies.remove(enemy)
    
    # Aktualizacja pocisków
    for bullet in bullets[:]:
        bullet.update()
        
        # Usuń pocisk jeśli wyjdzie poza ekran
        if (bullet.x < 0 or bullet.x > SCREEN_WIDTH or
            bullet.y < 0 or bullet.y > SCREEN_HEIGHT):
            bullets.remove(bullet)
    
    # Kolizje pocisków z wrogami
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
    
    player.draw(screen)
    
    for enemy in enemies:
        enemy.draw(screen)
    
    for bullet in bullets:
        bullet.draw(screen)
    
    pygame.display.flip()
    clock.tick(60)

pygame.quit()