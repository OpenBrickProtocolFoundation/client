import pygame

from tetrion import Event
from tetrion import EventType
from tetrion import Key
from tetrion import Tetrion


def main() -> None:
    frame = 0

    with Tetrion() as tetrion:
        pygame.init()

        RECT_SIZE = 30
        size = (RECT_SIZE * tetrion.width, (RECT_SIZE + 2) * tetrion.height)
        screen = pygame.display.set_mode(size)

        COLORS = [(0, 0, 0),
                  (0, 240, 240),
                  (0, 0, 240),
                  (240, 160, 0),
                  (240, 240, 0),
                  (0, 240, 0),
                  (160, 0, 240),
                  (240, 0, 0)]

        done = False

        clock = pygame.time.Clock()

        while not done:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    done = True
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        done = True
                    elif event.key == pygame.K_a:
                        tetrion.enqueue_event(Event(key=Key.LEFT, type=EventType.PRESSED, frame=frame))
                    elif event.key == pygame.K_d:
                        tetrion.enqueue_event(Event(key=Key.RIGHT, type=EventType.PRESSED, frame=frame))
                    elif event.key == pygame.K_SPACE:
                        tetrion.enqueue_event(Event(key=Key.DROP, type=EventType.PRESSED, frame=frame))

            tetrion.simulate_up_until(frame)
            screen.fill((100, 100, 100))

            matrix = tetrion.matrix()
            for y, row in enumerate(matrix.rows):
                for x, mino in enumerate(row):
                    pygame.draw.rect(screen, COLORS[mino.value],
                                     pygame.Rect(x * RECT_SIZE, y * RECT_SIZE, RECT_SIZE, RECT_SIZE))

            active_tetromino = tetrion.try_get_active_tetromino()
            if active_tetromino is not None:
                for position in active_tetromino.mino_positions:
                    x, y = position.x, position.y
                    pygame.draw.rect(screen, COLORS[active_tetromino.type.value],
                                     pygame.Rect(x * RECT_SIZE, y * RECT_SIZE, RECT_SIZE, RECT_SIZE))

            game_font = pygame.font.Font(None, 30)
            clock.tick(60)
            fps_counter = game_font.render(f"FPS: {int(clock.get_fps())} (frame {frame})", True, (255, 255, 255))

            screen.blit(fps_counter, (5, 5 + tetrion.height * RECT_SIZE))

            pygame.display.flip()
            frame += 1

        pygame.quit()


if __name__ == "__main__":
    main()
