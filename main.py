import logging
import socket
import struct
import sys
import threading
import time
from enum import Enum
from typing import NamedTuple
from typing import Optional

import pygame
import select

import controls
from tetrion import Event
from tetrion import EventType
from tetrion import Key
from tetrion import LobbyServerConnection
from tetrion import Tetrion
from tetrion import Tetromino
from tetrion import Vec2

COLORS = [
    (0, 0, 0),
    (0, 240, 240),
    (0, 0, 240),
    (240, 160, 0),
    (240, 240, 0),
    (0, 240, 0),
    (160, 0, 240),
    (240, 0, 0),
]

GHOST_COLORS = [
    (0, 0, 0),
    (0, 80, 80),
    (0, 0, 80),
    (80, 50, 0),
    (80, 80, 0),
    (0, 80, 0),
    (50, 0, 80),
    (80, 0, 0),
]

RECT_SIZE = 30

done = False


def send_event_buffer(target: socket.socket, events: list[Event], frame: int) -> None:
    # print(f"sending event buffer: {frame=}, {events=}")
    payload_size = 9 + 10 * len(events)
    data = struct.pack("!BHQB", 0, payload_size, frame, len(events))
    for event in events:
        data += struct.pack("!BBQ", event.key.value, event.type.value, event.frame)
    target.send(data)


def render_tetrion(screen: pygame.Surface, position: Vec2, tetrion: Tetrion) -> None:
    matrix = tetrion.matrix()
    for y, row in enumerate(matrix.rows):
        for x, mino in enumerate(row):
            pygame.draw.rect(screen, COLORS[mino.value],
                             pygame.Rect(position.x + x * RECT_SIZE, position.y + y * RECT_SIZE, RECT_SIZE, RECT_SIZE))

    def render_tetromino(tetromino: Tetromino, colors: list[tuple[int, int, int]]) -> None:
        for pos in tetromino.mino_positions:
            x, y = pos.x, pos.y
            pygame.draw.rect(screen, colors[tetromino.type.value],
                             pygame.Rect(position.x + x * RECT_SIZE, position.y + y * RECT_SIZE, RECT_SIZE, RECT_SIZE))

    ghost_tetromino = tetrion.try_get_ghost_tetromino()
    if ghost_tetromino is not None:
        render_tetromino(ghost_tetromino, GHOST_COLORS)

    active_tetromino = tetrion.try_get_active_tetromino()
    if active_tetromino is not None:
        render_tetromino(active_tetromino, COLORS)


class MessageType(Enum):
    HEARTBEAT = 0
    GRID_STATE = 1
    GAME_START = 2
    EVENT_BROADCAST = 3


class MessageHeader(NamedTuple):
    type_: MessageType
    payload_size: int


class ClientEvents(NamedTuple):
    client_id: int
    events: list[Event]


class BroadcastMessage(NamedTuple):
    frame: int
    events_per_client: list[ClientEvents]


class GameStartMessage(NamedTuple):
    client_id: int
    start_frame: int
    random_seed: int


message_queue: list[BroadcastMessage | GameStartMessage] = []


def keep_receiving(server_socket: socket.socket) -> None:
    global done
    buffer = bytearray()
    current_header: Optional[MessageHeader] = None
    print("entering main loop in keep_receiving()")
    while not done:
        ready = select.select([server_socket], [], [], 0.5)
        if not ready[0]:
            continue
        try:
            data = server_socket.recv(4096)
        except BlockingIOError:
            continue
        except ConnectionAbortedError:
            # server has gone :'(
            print("server has disconnected", file=sys.stderr)
            break
        except ConnectionResetError:
            # most likely we stopped the connection
            print("ConnectionResetError", file=sys.stderr)
            break

        buffer.extend(data)
        if current_header is None and len(buffer) >= 3:
            type_, payload_size = struct.unpack("!BH", buffer[:3])
            buffer = buffer[3:]
            current_header = MessageHeader(type_=MessageType(type_), payload_size=payload_size)
        if current_header is not None and len(buffer) >= current_header.payload_size:
            match current_header.type_:
                case MessageType.GAME_START:
                    client_id, start_frame, random_seed = struct.unpack("!BQQ", buffer[:17])
                    buffer = buffer[17:]
                    game_start_message = GameStartMessage(client_id, start_frame, random_seed)
                    # print(game_start_message)
                    message_queue.append(game_start_message)
                case MessageType.EVENT_BROADCAST:
                    message_frame, num_clients = struct.unpack("!QB", buffer[:9])
                    buffer = buffer[9:]
                    events_per_client: list[ClientEvents] = []
                    for _ in range(num_clients):
                        client_id, event_count = struct.unpack("!BB", buffer[:2])
                        buffer = buffer[2:]
                        events: list[Event] = []
                        for _ in range(event_count):
                            key, event_type, event_frame = struct.unpack("!BBQ", buffer[:10])
                            buffer = buffer[10:]
                            events.append(Event(Key(key), EventType(event_type), event_frame))
                        events_per_client.append(ClientEvents(client_id, events))
                    broadcast_message = BroadcastMessage(message_frame, events_per_client)
                    message_queue.append(broadcast_message)
                case _:
                    raise Exception(f"invalid message type: {current_header.type_.name}")
            current_header = None

    print("main loop ended in keep_receiving()")


_LOBBY_URL = "http://127.0.0.1:5000"


def main() -> None:
    with LobbyServerConnection("127.0.0.1", 5000) as connection:
        with connection.get_lobby_list() as lobby_list:
            num_lobbies = len(lobby_list.lobbies)
        with (
                connection.authenticate_user("r00tifant", "sudo")
                if num_lobbies == 0
                else connection.authenticate_user("coder2k", "secret")
        ) as user:
            if num_lobbies == 0:
                lobby = connection.create_lobby(user, "coder2k's lobby", 2)
                # todo: implement function that can retrieve the lobby info of a given lobby
                while True:
                    with connection.get_lobby_list() as lobby_list:
                        lobby_info = lobby_list.lobbies[0]
                        lobby_details = connection.get_lobby_details(lobby_info, user)
                    # if len(lobby_details.client_infos) == 0:
                    #     logging.debug("second player not present yet...")
                    #     time.sleep(1.0)
                    #     continue
                    gameserver_port = connection.start_lobby(user, lobby)
                    if gameserver_port is not None:
                        logging.debug("game started")
                        break
                    logging.debug("starting the game failed...")
                    time.sleep(1.0)
            else:
                assert num_lobbies == 1
                with connection.get_lobby_list() as lobby_list:
                    lobby_info = lobby_list.lobbies[0]
                    lobby = connection.join(lobby_info, user)
                logging.debug("joined lobby")
                gameserver_port = connection.set_ready(lobby, user)
                logging.debug("game started")

            print(f"active lobbies: {connection.get_lobby_list()}")

            global done

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as gameserver_socket:
                print("trying to connect to gameserver...")
                gameserver_socket.connect(("127.0.0.1", gameserver_port))
                gameserver_socket.setblocking(False)
                worker_thread = threading.Thread(target=keep_receiving, args=(gameserver_socket,))
                worker_thread.start()
                print("connected to gameserver")

                while len(message_queue) == 0:
                    time.sleep(0.1)

                game_start_message = message_queue.pop(0)
                assert isinstance(game_start_message, GameStartMessage)

                client_id = game_start_message.client_id
                seed = game_start_message.random_seed

                with Tetrion(seed) as tetrion, Tetrion(seed) as other_tetrion:
                    pygame.init()
                    size = (RECT_SIZE * tetrion.width * 2, (RECT_SIZE + 2) * tetrion.height)
                    screen = pygame.display.set_mode(size)

                    frame = 0

                    clock = pygame.time.Clock()

                    event_buffer: list[Event] = []

                    other_client_frame: Optional[int] = None

                    start_time = time.monotonic()

                    while not done:
                        for event in pygame.event.get():
                            if event.type == pygame.QUIT:
                                done = True
                            elif event.type == pygame.KEYDOWN:
                                if event.key == controls.QUIT:
                                    done = True
                                elif event.key == controls.LEFT:
                                    input_event = Event(key=Key.LEFT, type=EventType.PRESSED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.RIGHT:
                                    input_event = Event(key=Key.RIGHT, type=EventType.PRESSED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.DOWN:
                                    input_event = Event(key=Key.DOWN, type=EventType.PRESSED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.ROTATE_COUNTER_CLOCKWISE:
                                    input_event = Event(key=Key.ROTATE_CCW, type=EventType.PRESSED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.ROTATE_CLOCKWISE:
                                    input_event = Event(key=Key.ROTATE_CW, type=EventType.PRESSED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.DROP:
                                    input_event = Event(key=Key.DROP, type=EventType.PRESSED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                            elif event.type == pygame.KEYUP:
                                if event.key == controls.LEFT:
                                    input_event = Event(key=Key.LEFT, type=EventType.RELEASED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.RIGHT:
                                    input_event = Event(key=Key.RIGHT, type=EventType.RELEASED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.DOWN:
                                    input_event = Event(key=Key.DOWN, type=EventType.RELEASED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.ROTATE_COUNTER_CLOCKWISE:
                                    input_event = Event(key=Key.ROTATE_CCW, type=EventType.RELEASED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.ROTATE_CLOCKWISE:
                                    input_event = Event(key=Key.ROTATE_CW, type=EventType.RELEASED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)
                                elif event.key == controls.DROP:
                                    input_event = Event(key=Key.DROP, type=EventType.RELEASED, frame=frame)
                                    tetrion.enqueue_event(input_event)
                                    event_buffer.append(input_event)

                        if frame > 0:
                            tetrion.simulate_up_until(frame - 1)

                        while len(message_queue) > 0:
                            broadcast_message = message_queue.pop(0)
                            assert isinstance(broadcast_message, BroadcastMessage)
                            assert len(broadcast_message.events_per_client) >= 1
                            other_client_frame = broadcast_message.frame
                            # for input_event in broadcast_message.events_per_client[1 if client_id == 0 else 0].events:
                            #     other_tetrion.enqueue_event(input_event)

                        if frame > 30 and other_client_frame is not None:
                            other_tetrion.simulate_up_until(min(frame - 30, other_client_frame))

                        screen.fill((100, 100, 100))

                        render_tetrion(screen, Vec2(0, 0), tetrion)
                        render_tetrion(screen, Vec2(tetrion.width * RECT_SIZE, 0), other_tetrion)

                        clock.tick()
                        fps = int(clock.get_fps())

                        game_font = pygame.font.Font(None, 30)
                        fps_counter = game_font.render(f"{fps} FPS, frame {frame}", True, (255, 255, 255))

                        screen.blit(fps_counter, (5, 5 + tetrion.height * RECT_SIZE))

                        pygame.display.flip()

                        elapsed = time.monotonic() - start_time
                        new_frame = int(elapsed / (1.0 / 60.0))

                        while frame < new_frame - 1:
                            if frame % 15 == 0:
                                send_event_buffer(gameserver_socket, event_buffer, frame)
                                event_buffer.clear()
                            frame += 1

                print("main loop ended")

                worker_thread.join()

                gameserver_socket.close()

                pygame.quit()

            connection.destroy_lobby(user, lobby)


if __name__ == "__main__":
    main()
