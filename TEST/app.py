import pygame
import random
import math

# Inicjalizacja Pygame
pygame.init()

# Stałe
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
PLAYER_SIZE = 30
ENEMY_SIZE = 20
BULLET_SIZE = 5
PLAYER_SPEED = 5
BULLET_SPEED = 10
ENEMY_SPAWN_RATE = 60  # co ile klatek spawnuje sie wrog
MIN_ENEMY_DISTANCE = 150  # minimalna odleglosc od gracza do spawnowania

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
        
    def draw(self, screen):
        pygame.draw.rect(screen, GREEN, (self.x - self.size//2, self.y - self.size//2, self.size, self.size))
        
    def move(self, dx, dy):
        self.x += dx * self.speed
        self.y += dy * self.speed
        
        # Ograniczenia ekranu
        self.x = max(self.size//2, min(SCREEN_WIDTH - self.size//2, self.x))
        self.y = max(self.size//2, min(SCREEN_HEIGHT - self.size//2, self.y))

# Klasa wroga
class Enemy:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.size = ENEMY_SIZE
        self.speed = random.uniform(1.0, 3.0)
        
    def draw(self, screen):
        pygame.draw.rect(screen, RED, (self.x - self.size//2, self.y - self.size//2, self.size, self.size))
        
    def move_towards_player(self, player_x, player_y):
        # Oblicz wektor kierunku do gracza
        dx = player_x - self.x
        dy = player_y - self.y
        distance = max(1, math.sqrt(dx*dx + dy*dy))
        
        # Normalizuj wektor i przesuwaj wroga
        dx /= distance
        dy /= distance
        
        self.x += dx * self.speed
        self.y += dy * self.speed

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
        
        # Normalizuj wektor i ustaw predkosc
        self.dx = (dx / distance) * BULLET_SPEED
        self.dy = (dy / distance) * BULLET_SPEED
        
    def draw(self, screen):
        pygame.draw.circle(screen, BLUE, (int(self.x), int(self.y)), self.size)
        
    def update(self):
        self.x += self.dx
        self.y += self.dy
        
    def is_out_of_bounds(self):
        return (self.x < 0 or self.x > SCREEN_WIDTH or 
                self.y < 0 or self.y > SCREEN_HEIGHT)

# Inicjalizacja gracza
player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)

# Listy obiektow
enemies = []
bullets = []

# Zmienna do liczenia klatek
frame_count = 0

# Główna pętla gry
running = True
while running:
    # Obsługa zdarzeń
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        
        # Strzelanie po kliknięciu myszy
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:  # LPM
            mouse_x, mouse_y = pygame.mouse.get_pos()
            bullets.append(Bullet(player.x, player.y, mouse_x, mouse_y))
    
    # Pobieranie stanu klawiszy
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
    frame_count += 1
    if frame_count >= ENEMY_SPAWN_RATE:
        frame_count = 0
        
        # Losowe położenie poza ekranem z minimalną odległością
        side = random.choice(['top', 'bottom', 'left', 'right'])
        if side == 'top':
            x = random.randint(0, SCREEN_WIDTH)
            y = -MIN_ENEMY_DISTANCE
        elif side == 'bottom':
            x = random.randint(0, SCREEN_WIDTH)
            y = SCREEN_HEIGHT + MIN_ENEMY_DISTANCE
        elif side == 'left':
            x = -MIN_ENEMY_DISTANCE
            y = random.randint(0, SCREEN_HEIGHT)
        else:  # right
            x = SCREEN_WIDTH + MIN_ENEMY_DISTANCE
            y = random.randint(0, SCREEN_HEIGHT)
            
        enemies.append(Enemy(x, y))
    
    # Aktualizacja wrogów (ruch do gracza)
    for enemy in enemies:
        enemy.move_towards_player(player.x, player.y)
    
    # Aktualizacja pocisków
    for bullet in bullets[:]:
        bullet.update()
        if bullet.is_out_of_bounds():
            bullets.remove(bullet)
    
    # Kolizje
    # Pociski z wrogami
    for bullet in bullets[:]:
        for enemy in enemies[:]:
            distance = math.sqrt((bullet.x - enemy.x)**2 + (bullet.y - enemy.y)**2)
            if distance < (BULLET_SIZE + ENEMY_SIZE)//2:
                if bullet in bullets:
                    bullets.remove(bullet)
                if enemy in enemies:
                    enemies.remove(enemy)
                break
    
    # Wrogowie z graczem
    for enemy in enemies[:]:
        distance = math.sqrt((player.x - enemy.x)**2 + (player.y - enemy.y)**2)
        if distance < (PLAYER_SIZE + ENEMY_SIZE)//2:
            print("Game Over!")
            running = False
            
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