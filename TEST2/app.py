import pygame
import sys
import random
import math

# Initialize pygame
pygame.init()

# Constants
SCREEN_WIDTH = 1024
SCREEN_HEIGHT = 768
FPS = 60

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
YELLOW = (255, 255, 0)
GRAY = (128, 128, 128)
ORANGE = (255, 165, 0)
PURPLE = (128, 0, 128)

# Game states
class GameState:
    MENU = 0
    PLAYING = 1
    PAUSED = 2
    SHOP = 3
    GAME_OVER = 4

# Player class
class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.radius = 20
        self.speed = 5
        self.health = 100
        self.max_health = 100
        self.money = 0
        self.shoot_cooldown = 0
        self.shoot_delay = 10
        self.damage = 10
        self.auto_shoot = False
        self.shield = False
        self.shield_duration = 0
        self.rocket_launcher = False
        self.rocket_cooldown = 0
        self.rocket_delay = 30
        
    def update(self, keys, enemies):
        # Movement
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            self.y -= self.speed
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            self.y += self.speed
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            self.x -= self.speed
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            self.x += self.speed
        
        # Keep player on screen
        self.x = max(self.radius, min(SCREEN_WIDTH - self.radius, self.x))
        self.y = max(self.radius, min(SCREEN_HEIGHT - self.radius, self.y))
        
        # Update cooldowns
        if self.shoot_cooldown > 0:
            self.shoot_cooldown -= 1
        
        if self.rocket_cooldown > 0:
            self.rocket_cooldown -= 1
        
        # Update shield
        if self.shield_duration > 0:
            self.shield_duration -= 1
            if self.shield_duration <= 0:
                self.shield = False
        
        # Shooting logic
        if self.auto_shoot:
            self.shoot_auto(enemies)
        
    def shoot_auto(self, enemies):
        if self.shoot_cooldown <= 0:
            # Find nearest enemy
            nearest_enemy = None
            min_distance = float('inf')
            for enemy in enemies:
                distance = math.sqrt((self.x - enemy.x)**2 + (self.y - enemy.y)**2)
                if distance < min_distance:
                    min_distance = distance
                    nearest_enemy = enemy
            
            if nearest_enemy and min_distance < 300:  # Only shoot if enemy is close enough
                self.shoot(nearest_enemy.x, nearest_enemy.y)
    
    def shoot(self, target_x, target_y):
        if self.shoot_cooldown <= 0:
            # Calculate direction vector
            dx = target_x - self.x
            dy = target_y - self.y
            distance = max(1, math.sqrt(dx*dx + dy*dy))
            
            # Normalize and add some randomness
            dx /= distance
            dy /= distance
            
            # Add slight randomness to bullet direction
            dx += random.uniform(-0.1, 0.1)
            dy += random.uniform(-0.1, 0.1)
            
            # Create bullet
            bullets.append(Bullet(self.x, self.y, dx, dy, self.damage))
            
            if self.rocket_launcher:
                # Create rocket
                rockets.append(Rocket(self.x, self.y, dx, dy, self.damage * 2))
                
            self.shoot_cooldown = self.shoot_delay
            
    def take_damage(self, damage):
        if not self.shield:
            self.health -= damage
            return True
        return False
    
    def draw(self, screen):
        # Draw player
        pygame.draw.circle(screen, BLUE, (int(self.x), int(self.y)), self.radius)
        
        # Draw shield if active
        if self.shield:
            pygame.draw.circle(screen, YELLOW, (int(self.x), int(self.y)), self.radius + 5, 2)
        
        # Draw health bar
        bar_width = 40
        bar_height = 5
        pygame.draw.rect(screen, RED, (self.x - bar_width//2, self.y - self.radius - 10, bar_width, bar_height))
        pygame.draw.rect(screen, GREEN, (self.x - bar_width//2, self.y - self.radius - 10, bar_width * (self.health / self.max_health), bar_height))

# Bullet class
class Bullet:
    def __init__(self, x, y, dx, dy, damage):
        self.x = x
        self.y = y
        self.dx = dx
        self.dy = dy
        self.speed = 10
        self.damage = damage
        self.radius = 3
        
    def update(self):
        self.x += self.dx * self.speed
        self.y += self.dy * self.speed
        
    def draw(self, screen):
        pygame.draw.circle(screen, WHITE, (int(self.x), int(self.y)), self.radius)
        
    def is_off_screen(self):
        return (self.x < 0 or self.x > SCREEN_WIDTH or 
                self.y < 0 or self.y > SCREEN_HEIGHT)

# Rocket class
class Rocket:
    def __init__(self, x, y, dx, dy, damage):
        self.x = x
        self.y = y
        self.dx = dx
        self.dy = dy
        self.speed = 7
        self.damage = damage
        self.radius = 6
        self.trail = []
        self.max_trail_length = 10
        
    def update(self):
        # Add current position to trail
        self.trail.append((self.x, self.y))
        if len(self.trail) > self.max_trail_length:
            self.trail.pop(0)
        
        self.x += self.dx * self.speed
        self.y += self.dy * self.speed
        
    def draw(self, screen):
        # Draw trail
        for i, (trail_x, trail_y) in enumerate(self.trail):
            alpha = int(255 * (i / len(self.trail)))
            radius = max(1, int(self.radius * (i / len(self.trail))))
            pygame.draw.circle(screen, ORANGE, (int(trail_x), int(trail_y)), radius)
        
        # Draw rocket
        pygame.draw.circle(screen, RED, (int(self.x), int(self.y)), self.radius)
        
    def is_off_screen(self):
        return (self.x < 0 or self.x > SCREEN_WIDTH or 
                self.y < 0 or self.y > SCREEN_HEIGHT)

# Enemy class
class Enemy:
    def __init__(self, x, y, enemy_type=1):
        self.x = x
        self.y = y
        self.type = enemy_type
        self.speed = random.uniform(1.0, 3.0) if enemy_type == 1 else random.uniform(0.5, 2.0)
        self.health = 20 if enemy_type == 1 else 40
        self.max_health = self.health
        self.damage = 10 if enemy_type == 1 else 20
        self.radius = 15 if enemy_type == 1 else 20
        self.shoot_cooldown = random.randint(30, 90)
        self.shoot_delay = 60
        self.last_direction_change = 0
        self.direction_change_interval = 120
        
    def update(self, player):
        # Move towards player
        dx = player.x - self.x
        dy = player.y - self.y
        distance = max(1, math.sqrt(dx*dx + dy*dy))
        
        # Normalize direction
        dx /= distance
        dy /= distance
        
        # Add some randomness to movement
        dx += random.uniform(-0.1, 0.1)
        dy += random.uniform(-0.1, 0.1)
        
        self.x += dx * self.speed
        self.y += dy * self.speed
        
        # Shooting logic
        if self.shoot_cooldown <= 0:
            # Shoot at player
            bullets.append(Bullet(self.x, self.y, dx, dy, self.damage))
            self.shoot_cooldown = self.shoot_delay
        else:
            self.shoot_cooldown -= 1
        
        # Change direction occasionally
        if self.last_direction_change <= 0:
            self.direction_change_interval = random.randint(60, 180)
            self.last_direction_change = self.direction_change_interval
        else:
            self.last_direction_change -= 1
        
    def draw(self, screen):
        # Draw enemy
        color = RED if self.type == 1 else PURPLE
        pygame.draw.circle(screen, color, (int(self.x), int(self.y)), self.radius)
        
        # Draw health bar
        bar_width = 30
        bar_height = 4
        pygame.draw.rect(screen, RED, (self.x - bar_width//2, self.y - self.radius - 8, bar_width, bar_height))
        pygame.draw.rect(screen, GREEN, (self.x - bar_width//2, self.y - self.radius - 8, bar_width * (self.health / self.max_health), bar_height))

# Shop item class
class ShopItem:
    def __init__(self, name, cost, description, effect):
        self.name = name
        self.cost = cost
        self.description = description
        self.effect = effect
        
    def apply_effect(self, player):
        if self.effect == 'auto_shoot':
            player.auto_shoot = True
        elif self.effect == 'shield':
            player.shield = True
            player.shield_duration = 300  # 5 seconds at 60 FPS
        elif self.effect == 'rocket_launcher':
            player.rocket_launcher = True
        elif self.effect == 'health_upgrade':
            player.max_health += 20
            player.health = min(player.max_health, player.health + 20)
        elif self.effect == 'damage_upgrade':
            player.damage += 5
        elif self.effect == 'speed_upgrade':
            player.speed += 1
        
# Game class
class Game:
    def __init__(self):
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("Advanced Top-Down Shooter")
        self.clock = pygame.time.Clock()
        self.state = GameState.PLAYING
        
        # Game objects
        self.player = Player(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
        self.enemies = []
        self.bullets = []
        self.rockets = []
        self.shop_items = [
            ShopItem("Auto-Shoot", 100, "Automatically shoot at enemies", "auto_shoot"),
            ShopItem("Shield", 200, "Temporary shield protection", "shield"),
            ShopItem("Rocket Launcher", 300, "Shoot rockets instead of bullets", "rocket_launcher"),
            ShopItem("Health Upgrade", 150, "Increase max health by 20", "health_upgrade"),
            ShopItem("Damage Upgrade", 120, "Increase bullet damage by 5", "damage_upgrade"),
            ShopItem("Speed Upgrade", 180, "Increase movement speed", "speed_upgrade")
        ]
        
        # Game settings
        self.enemy_spawn_timer = 0
        self.enemy_spawn_delay = 60  # Spawn enemies every 60 frames
        self.score = 0
        self.wave = 1
        self.enemies_killed = 0
        
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if self.state == GameState.PLAYING:
                        self.state = GameState.PAUSED
                    elif self.state == GameState.PAUSED:
                        self.state = GameState.PLAYING
                    elif self.state == GameState.SHOP:
                        self.state = GameState.PLAYING
                
                # Shop controls
                if self.state == GameState.SHOP:
                    if event.key == pygame.K_1:
                        self.buy_item(0)
                    elif event.key == pygame.K_2:
                        self.buy_item(1)
                    elif event.key == pygame.K_3:
                        self.buy_item(2)
                    elif event.key == pygame.K_4:
                        self.buy_item(3)
                    elif event.key == pygame.K_5:
                        self.buy_item(4)
                    elif event.key == pygame.K_6:
                        self.buy_item(5)
                    
            if event.type == pygame.MOUSEBUTTONDOWN and self.state == GameState.SHOP:
                # Handle shop item clicks
                mouse_x, mouse_y = pygame.mouse.get_pos()
                for i, item in enumerate(self.shop_items):
                    # Simple click detection (would be more complex in real game)
                    if 100 + i * 150 <= mouse_x <= 250 + i * 150 and 100 <= mouse_y <= 150:
                        self.buy_item(i)
        
        return True
    
    def buy_item(self, item_index):
        if item_index < len(self.shop_items):
            item = self.shop_items[item_index]
            if self.player.money >= item.cost:
                self.player.money -= item.cost
                item.apply_effect(self.player)
                print(f"Bought: {item.name}")
    
    def spawn_enemy(self):
        # Spawn enemies at random edges
        side = random.randint(0, 3)
        if side == 0:  # Top
            x = random.randint(0, SCREEN_WIDTH)
            y = -20
        elif side == 1:  # Right
            x = SCREEN_WIDTH + 20
            y = random.randint(0, SCREEN_HEIGHT)
        elif side == 2:  # Bottom
            x = random.randint(0, SCREEN_WIDTH)
            y = SCREEN_HEIGHT + 20
        else:  # Left
            x = -20
            y = random.randint(0, SCREEN_HEIGHT)
        
        enemy_type = 1 if self.wave < 5 else (1 if random.random() < 0.7 else 2)
        self.enemies.append(Enemy(x, y, enemy_type))
    
    def update(self):
        if self.state != GameState.PLAYING:
            return
        
        # Spawn enemies
        self.enemy_spawn_timer += 1
        if self.enemy_spawn_timer >= self.enemy_spawn_delay:
            self.spawn_enemy()
            self.enemy_spawn_timer = 0
            
        # Update player
        keys = pygame.key.get_pressed()
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            self.player.y -= self.player.speed
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            self.player.y += self.player.speed
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            self.player.x -= self.player.speed
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            self.player.x += self.player.speed
        
        # Keep player on screen
        self.player.x = max(0, min(SCREEN_WIDTH, self.player.x))
        self.player.y = max(0, min(SCREEN_HEIGHT, self.player.y))
        
        # Update bullets
        for bullet in self.bullets[:]:
            if bullet.is_off_screen():
                self.bullets.remove(bullet)
                continue
            
            # Check collision with enemies
            for enemy in self.enemies[:]:
                dx = bullet.x - enemy.x
                dy = bullet.y - enemy.y
                distance = math.sqrt(dx*dx + dy*dy)
                
                if distance < enemy.radius:
                    enemy.health -= self.player.damage
                    if enemy.health <= 0:
                        self.enemies.remove(enemy)
                        self.player.money += 10
                        self.score += 10
                        self.enemies_killed += 1
                    
                    if bullet in self.bullets:
                        self.bullets.remove(bullet)
                    break
        
        # Update rockets
        for rocket in self.rockets[:]:
            if rocket.is_off_screen():
                self.rockets.remove(rocket)
                continue
            
            # Check collision with enemies
            for enemy in self.enemies[:]:
                dx = rocket.x - enemy.x
                dy = rocket.y - enemy.y
                distance = math.sqrt(dx*dx + dy*dy)
                
                if distance < enemy.radius:
                    enemy.health -= 50  # Rocket damage
                    if enemy.health <= 0:
                        self.enemies.remove(enemy)
                        self.player.money += 10
                        self.score += 10
                        self.enemies_killed += 1
                    
                    if rocket in self.rockets:
                        self.rockets.remove(rocket)
                    break
        
        # Update enemies
        for enemy in self.enemies[:]:
            enemy.update(self.player)
            
            # Check collision with player
            dx = self.player.x - enemy.x
            dy = self.player.y - enemy.y
            distance = math.sqrt(dx*dx + dy*dy)
            
            if distance < self.player.radius + enemy.radius:
                self.player.health -= enemy.damage
                if self.player.health <= 0:
                    print("Game Over!")
                    return False  # Game over
        
        # Check wave progression
        if self.enemies_killed >= self.wave * 10:
            self.wave += 1
            self.enemy_spawn_delay = max(20, self.enemy_spawn_delay - 5)
            print(f"Wave {self.wave}! Enemies spawn faster.")
        
        return True
    
    def draw(self):
        self.screen.fill((0, 0, 0))
        
        if self.state == GameState.PLAYING:
            # Draw game objects
            for bullet in self.bullets:
                pygame.draw.circle(self.screen, (255, 255, 0), (int(bullet.x), int(bullet.y)), 3)
            
            for rocket in self.rockets:
                pygame.draw.circle(self.screen, (255, 100, 0), (int(rocket.x), int(rocket.y)), 6)
            
            for enemy in self.enemies:
                enemy.draw(self.screen)
            
            # Draw player
            pygame.draw.circle(self.screen, (0, 255, 0), (int(self.player.x), int(self.player.y)), self.player.radius)
            
            # Draw UI
            font = pygame.font.SysFont(None, 36)
            score_text = font.render(f"Score: {self.score}", True, (255, 255, 255))
            money_text = font.render(f"Money: {self.player.money}", True, (255, 255, 255))
            wave_text = font.render(f"Wave: {self.wave}", True, (255, 255, 255))
            
            self.screen.blit(score_text, (10, 10))
            self.screen.blit(money_text, (10, 50))
            self.screen.blit(wave_text, (10, 90))
        
        elif self.state == GameState.PAUSED:
            # Draw pause screen
            font = pygame.font.SysFont(None, 72)
            text = font.render("PAUSED", True, (255, 255, 255))
            text_rect = text.get_rect(center=(SCREEN_WIDTH//2, SCREEN_HEIGHT//2))
            self.screen.blit(text, text_rect)
            
            small_font = pygame.font.SysFont(None, 36)
            continue_text = small_font.render("Press ESC to continue", True, (255, 255, 255))
            continue_rect = continue_text.get_rect(center=(SCREEN_WIDTH//2, SCREEN_HEIGHT//2 + 60))
            self.screen.blit(continue_text, continue_rect)
        
        elif self.state == GameState.SHOP:
            # Draw shop screen
            font = pygame.font.SysFont(None, 72)
            text = font.render("SHOP", True, (255, 255, 255))
            text_rect = text.get_rect(center=(SCREEN_WIDTH//2, 50))
            self.screen.blit(text, text_rect)
            
            # Draw shop items
            small_font = pygame.font.SysFont(None, 24)
            for i, item in enumerate(self.shop_items):
                color = (255, 255, 255) if self.player.money >= item.cost else (100, 100, 100)
                name_text = small_font.render(item.name, True, color)
                cost_text = small_font.render(f"Cost: {item.cost}", True, color)
                desc_text = small_font.render(item.description, True, color)
                
                self.screen.blit(name_text, (100 + i * 150, 100))
                self.screen.blit(cost_text, (100 + i * 150, 130))
                self.screen.blit(desc_text, (100 + i * 150, 160))
            
            # Draw instructions
            instr_font = pygame.font.SysFont(None, 24)
            instr_text = instr_font.render("Press numbers 1-6 to buy items", True, (255, 255, 255))
            self.screen.blit(instr_text, (10, SCREEN_HEIGHT - 30))
        
        pygame.display.flip()
    
    def run(self):
        clock = pygame.time.Clock()
        running = True
        
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if self.state == GameState.PLAYING:
                            self.state = GameState.PAUSED
                        elif self.state == GameState.PAUSED:
                            self.state = GameState.PLAYING
                        elif self.state == GameState.SHOP:
                            self.state = GameState.PLAYING
                    elif event.key == pygame.K_1 and self.state == GameState.SHOP:
                        # Buy item 1
                        if self.player.money >= self.shop_items[0].cost:
                            self.player.money -= self.shop_items[0].cost
                            # Apply upgrade
                            pass
                    elif event.key == pygame.K_2 and self.state == GameState.SHOP:
                        # Buy item 2
                        if self.player.money >= self.shop_items[1].cost:
                            self.player.money -= self.shop_items[1].cost
                            # Apply upgrade
                            pass
                    elif event.key == pygame.K_3 and self.state == GameState.SHOP:
                        # Buy item 3
                        if self.player.money >= self.shop_items[2].cost:
                            self.player.money -= self.shop_items[2].cost
                            # Apply upgrade
                            pass
                    elif event.key == pygame.K_4 and self.state == GameState.SHOP:
                        # Buy item 4
                        if self.player.money >= self.shop_items[3].cost:
                            self.player.money -= self.shop_items[3].cost
                            # Apply upgrade
                            pass
                    elif event.key == pygame.K_5 and self.state == GameState.SHOP:
                        # Buy item 5
                        if self.player.money >= self.shop_items[4].cost:
                            self.player.money -= self.shop_items[4].cost
                            # Apply upgrade
                            pass
                    elif event.key == pygame.K_6 and self.state == GameState.SHOP:
                        # Buy item 6
                        if self.player.money >= self.shop_items[5].cost:
                            self.player.money -= self.shop_items[5].cost
                            # Apply upgrade
                            pass
            
            if not self.update():
                running = False
            
            self.draw()
            clock.tick(60)
        
        pygame.quit()

if __name__ == "__main__":
    game = Game()
    game.run()