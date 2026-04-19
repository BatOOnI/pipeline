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
ENEMY_SPAWN_RATE = 2  # sekundy
ENEMY_HEALTH = 100
BULLET_DAMAGE = 50
ENEMY_SPEED_INCREASE_INTERVAL = 5  # sekundy
ENEMY_SPEED_INCREASE_FACTOR = 0.1

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
font = pygame.font.Font(None, 36)
small_font = pygame.font.Font(None, 24)
clock = pygame.time.Clock()

# Klasa gracza
class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.radius = PLAYER_RADIUS
        self.speed = PLAYER_SPEED
        self.health = 100
        
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
    
    def draw(self, screen):
        pygame.draw.circle(screen, BLUE, (int(self.x), int(self.y)), self.radius)
        # Rysowanie lufy
        mouse_x, mouse_y = pygame.mouse.get_pos()
        angle = math.atan2(mouse_y - self.y, mouse_x - self.x)
        end_x = self.x + math.cos(angle) * (self.radius + 10)
        end_y = self.y + math.sin(angle) * (self.radius + 10)
        pygame.draw.line(screen, BLACK, (self.x, self.y), (end_x, end_y), 3)

# Klasa pocisku
class Bullet:
    def __init__(self, x, y, target_x, target_y):
        self.x = x
        self.y = y
        self.radius = BULLET_RADIUS
        # Oblicz wektor kierunkowy
        dx = target_x - x
        dy = target_y - y
        distance = max(1, math.sqrt(dx*dx + dy*dy))
        self.dx = (dx / distance) * BULLET_SPEED
        self.dy = (dy / distance) * BULLET_SPEED
        
    def update(self):
        self.x += self.dx
        self.y += self.dy
        
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
        self.health = ENEMY_HEALTH
        
    def update(self, player_x, player_y, speed_factor):
        # Oblicz wektor kierunkowy do gracza
        dx = player_x - self.x
        dy = player_y - self.y
        distance = max(1, math.sqrt(dx*dx + dy*dy))
        
        # Poruszanie się w stronę gracza z mnożnikiem prędkości
        self.x += (dx / distance) * speed_factor
        self.y += (dy / distance) * speed_factor
        
    def draw(self, screen):
        pygame.draw.circle(screen, RED, (int(self.x), int(self.y)), self.radius)
        # Pasek życia
        bar_width = self.radius * 2
        bar_height = 5
        health_ratio = self.health / ENEMY_HEALTH
        pygame.draw.rect(screen, RED, (self.x - bar_width/2, self.y - self.radius - 10, bar_width, bar_height))
        pygame.draw.rect(screen, GREEN, (self.x - bar_width/2, self.y - self.radius - 10, bar_width * health_ratio, bar_height))

# Funkcja do generowania wrogów poza ekranem
def spawn_enemy():
    side = random.choice(['top', 'bottom', 'left', 'right'])
    if side == 'top':
        x = random.randint(0, SCREEN_WIDTH)
        y = -ENEMY_RADIUS
    elif side == 'bottom':
        x = random.randint(0, SCREEN_WIDTH)
        y = SCREEN_HEIGHT + ENEMY_RADIUS
    elif side == 'left':
        x = -ENEMY_RADIUS
        y = random.randint(0, SCREEN_HEIGHT)
    else:  # right
        x = SCREEN_WIDTH + ENEMY_RADIUS
        y = random.randint(0, SCREEN_HEIGHT)
    return Enemy(x, y)

# Funkcja do sprawdzania kolizji
def check_collision(obj1_x, obj1_y, obj2_x, obj2_y, radius1, radius2):
    distance = math.sqrt((obj1_x - obj2_x)**2 + (obj1_y - obj2_y)**2)
    return distance < (radius1 + radius2)

class Game:
    def __init__(self):
        self.player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
        self.bullets = []
        self.enemies = []
        self.score = 0
        self.game_over = False
        self.paused = False
        self.enemy_spawn_timer = 0
        self.last_speed_increase = 0
        self.enemy_speed_factor = 1.0
        
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and not self.game_over and not self.paused:
                # Strzelanie
                mouse_x, mouse_y = pygame.mouse.get_pos()
                self.bullets.append(Bullet(self.player.x, self.player.y, mouse_x, mouse_y))
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_r and self.game_over:
                    # Restart gry
                    self.__init__()
                elif event.key == pygame.K_ESCAPE:
                    # Pauza
                    self.paused = not self.paused
        return True
    
    def update(self, dt):
        if self.game_over or self.paused:
            return
        
        keys = pygame.key.get_pressed()
        self.player.move(keys)
        
        # Aktualizacja pocisków
        for bullet in self.bullets[:]:
            bullet.update()
            if bullet.is_off_screen():
                self.bullets.remove(bullet)
            
        # Spawnowanie wrogów
        self.enemy_spawn_timer += dt
        if self.enemy_spawn_timer >= ENEMY_SPAWN_RATE:
            self.enemies.append(spawn_enemy())
            self.enemy_spawn_timer = 0
        
        # Aktualizacja wrogów
        for enemy in self.enemies:
            enemy.update(self.player.x, self.player.y, self.enemy_speed_factor)
            
        # Kolizje pocisków z wrogami
        for bullet in self.bullets[:]:
            for enemy in self.enemies[:]:
                if check_collision(bullet.x, bullet.y, enemy.x, enemy.y, bullet.radius, enemy.radius):
                    enemy.health -= BULLET_DAMAGE
                    if enemy.health <= 0:
                        self.enemies.remove(enemy)
                        self.score += 10
                    try:
                        self.bullets.remove(bullet)
                    except ValueError:
                        pass
                    break
        
        # Kolizje wrogów z graczem
        for enemy in self.enemies[:]:
            if check_collision(self.player.x, self.player.y, enemy.x, enemy.y, self.player.radius, enemy.radius):
                self.game_over = True
                
        # Zwiększanie prędkości wrogów co 5 sekund
        self.last_speed_increase += dt
        if self.last_speed_increase >= ENEMY_SPEED_INCREASE_INTERVAL:
            self.enemy_speed_factor *= (1 + ENEMY_SPEED_INCREASE_FACTOR)
            self.last_speed_increase = 0
    
    def draw(self, screen):
        screen.fill(WHITE)
        
        if not self.game_over and not self.paused:
            # Rysowanie gracza
            self.player.draw(screen)
            
            # Rysowanie pocisków
            for bullet in self.bullets:
                bullet.draw(screen)
            
            # Rysowanie wrogów
            for enemy in self.enemies:
                enemy.draw(screen)
        
        # Rysowanie punktów
        score_text = font.render(f"Score: {self.score}", True, BLACK)
        screen.blit(score_text, (10, 10))
        
        # Rysowanie prędkości wrogów
        speed_text = small_font.render(f"Enemy Speed: {self.enemy_speed_factor:.2f}x", True, BLACK)
        screen.blit(speed_text, (10, 50))
        
        if self.game_over:
            # Ekran game over
            game_over_text = font.render("GAME OVER", True, RED)
            restart_text = small_font.render("Press R to Restart", True, BLACK)
            screen.blit(game_over_text, (SCREEN_WIDTH//2 - game_over_text.get_width()//2, SCREEN_HEIGHT//2 - 30))
            screen.blit(restart_text, (SCREEN_WIDTH//2 - restart_text.get_width()//2, SCREEN_HEIGHT//2 + 10))
        
        if self.paused:
            # Ekran pauzy
            pause_text = font.render("PAUSED", True, BLACK)
            screen.blit(pause_text, (SCREEN_WIDTH//2 - pause_text.get_width()//2, SCREEN_HEIGHT//2))
        
        pygame.display.flip()

# Główna pętla gry
game = Game()
running = True

while running:
    dt = clock.tick(60) / 1000.0  # Czas w sekundach
    
    running = game.handle_events()
    game.update(dt)
    game.draw(screen)

pygame.quit()
sys.exit()