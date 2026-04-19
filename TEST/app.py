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
        
    def draw(self, screen):
        pygame.draw.circle(screen, GREEN, (int(self.x), int(self.y)), self.size)
        
    def move(self, dx, dy):
        self.x += dx * self.speed
        self.y += dy * self.speed
        
        # Ograniczenia ekranu
        self.x = max(self.size, min(SCREEN_WIDTH - self.size, self.x))
        self.y = max(self.size, min(SCREEN_HEIGHT - self.size, self.y))

# Klasa wroga
class Enemy:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.size = ENEMY_SIZE
        self.speed = random.uniform(1.0, 3.0)
        
    def draw(self, screen):
        pygame.draw.circle(screen, RED, (int(self.x), int(self.y)), self.size)
        
    def move_towards_player(self, player_x, player_y):
        # Oblicz wektor kierunku do gracza
        dx = player_x - self.x
        dy = player_y - self.y
        distance = math.sqrt(dx*dx + dy*dy)
        
        if distance > 0:
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
        distance = math.sqrt(dx*dx + dy*dy)
        
        if distance > 0:
            self.dx = (dx / distance) * BULLET_SPEED
            self.dy = (dy / distance) * BULLET_SPEED
        else:
            self.dx = 0
            self.dy = 0
        
    def draw(self, screen):
        pygame.draw.circle(screen, BLUE, (int(self.x), int(self.y)), self.size)
        
    def update(self):
        self.x += self.dx
        self.y += self.dy
        
    def is_off_screen(self):
        return (self.x < -self.size or self.x > SCREEN_WIDTH + self.size or
                self.y < -self.size or self.y > SCREEN_HEIGHT + self.size)

# Inicjalizacja obiektów
game_running = True
player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
enemies = []
bullets = []
spawn_timer = 0
score = 0

# Główna pętla gry
while game_running:
    # Obsługa zdarzeń
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            game_running = False
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1:  # Lewy przycisk myszy
                mouse_x, mouse_y = pygame.mouse.get_pos()
                bullets.append(Bullet(player.x, player.y, mouse_x, mouse_y))
    
    # Pobranie stanu klawiszy
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
    spawn_timer += 1
    if spawn_timer >= ENEMY_SPAWN_RATE:
        spawn_timer = 0
        # Spawnuj z losowej strony ekranu
        side = random.choice(['top', 'bottom', 'left', 'right'])
        if side == 'top':
            x = random.randint(0, SCREEN_WIDTH)
            y = -ENEMY_SIZE
        elif side == 'bottom':
            x = random.randint(0, SCREEN_WIDTH)
            y = SCREEN_HEIGHT + ENEMY_SIZE
        elif side == 'left':
            x = -ENEMY_SIZE
            y = random.randint(0, SCREEN_HEIGHT)
        else:  # right
            x = SCREEN_WIDTH + ENEMY_SIZE
            y = random.randint(0, SCREEN_HEIGHT)
        
        enemies.append(Enemy(x, y))
    
    # Aktualizacja wrogów
    for enemy in enemies:
        enemy.move_towards_player(player.x, player.y)
    
    # Aktualizacja pocisków
    for bullet in bullets[:]:
        bullet.update()
        if bullet.is_off_screen():
            bullets.remove(bullet)
    
    # Kolizje
    # Pociski z wrogami
    for bullet in bullets[:]:
        for enemy in enemies[:]:
            distance = math.sqrt((bullet.x - enemy.x)**2 + (bullet.y - enemy.y)**2)
            if distance < bullet.size + enemy.size:
                if bullet in bullets:
                    bullets.remove(bullet)
                if enemy in enemies:
                    enemies.remove(enemy)
                score += 1
                break
    
    # Wrogowie z graczem
    for enemy in enemies[:]:
        distance = math.sqrt((player.x - enemy.x)**2 + (player.y - enemy.y)**2)
        if distance < player.size + enemy.size:
            game_running = False  # Koniec gry
            
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
    
    # Wyświetlanie punktów
    font = pygame.font.Font(None, 36)
    score_text = font.render(f"Score: {score}", True, BLACK)
    screen.blit(score_text, (10, 10))
    
    pygame.display.flip()
    clock.tick(60)

pygame.quit()