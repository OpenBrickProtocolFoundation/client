import logging
import socket
import struct
import sys
import threading
import time
from enum import auto
from enum import Enum
from typing import NamedTuple
from typing import Optional

import pygame
import select

import controls
from synchronized import Synchronized
from tetrion import _tetromino_get_mino_positions
from tetrion import Event
from tetrion import Key
from tetrion import Lobby
from tetrion import LobbyServerConnection
from tetrion import Rotation
from tetrion import Tetrion
from tetrion import Tetromino
from tetrion import TetrominoType
from tetrion import User
from tetrion import Vec2


class Mode(Enum):
    ONE_PLAYER = auto()
    TWO_PLAYERS = auto()


MODE = Mode.ONE_PLAYER

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


def send_heartbeat_message(target: socket.socket, frame: int, key_states_buffer: list[set[Key]]) -> None:
    print(f"frame = {frame}")
    assert len(key_states_buffer) == 15
    payload_size = 8 + len(key_states_buffer)
    buffer = struct.pack("!BHQ", MessageType.HEARTBEAT.value, payload_size, frame)
    for key_states in key_states_buffer:
        buffer += struct.pack("!B", sum(1 << key.value for key in key_states))
    target.send(buffer)


def render_tetrion(
        screen: pygame.Surface,
        position: Vec2,
        tetrion: Tetrion,
) -> None:
    def render_mino(
            x: int,
            y: int,
            fill_color: tuple[int, int, int],
            border_color: tuple[int, int, int],
    ) -> None:
        pygame.draw.rect(
            screen,
            fill_color,
            pygame.Rect(position.x + x * RECT_SIZE, position.y + y * RECT_SIZE, RECT_SIZE, RECT_SIZE)
        )
        pygame.draw.rect(
            screen,
            border_color,
            pygame.Rect(position.x + x * RECT_SIZE, position.y + y * RECT_SIZE, RECT_SIZE, RECT_SIZE),
            1
        )

    def render_tetromino(
            tetromino: Tetromino,
            offset: Vec2,
            fill_colors: list[tuple[int, int, int]],
            border_colors: list[tuple[int, int, int]],
    ) -> None:
        for pos in tetromino.mino_positions:
            render_mino(
                pos.x + offset.x,
                pos.y + offset.y,
                fill_colors[tetromino.type.value],
                border_colors[tetromino.type.value],
            )

    # render gray area left of the grid for the hold piece (6 columns wide)
    pygame.draw.rect(
        screen,
        (64, 64, 64),
        pygame.Rect(position.x, position.y, RECT_SIZE * 6, tetrion.height * RECT_SIZE)
    )

    hold_piece = tetrion.get_hold_piece()
    if hold_piece != TetrominoType.EMPTY:
        mino_positions = _tetromino_get_mino_positions(hold_piece, Rotation.NORTH)
        for pos in mino_positions:
            render_mino(
                pos.x + 1,
                pos.y + 1,
                COLORS[hold_piece.value],
                GHOST_COLORS[hold_piece.value],
            )

    # render grid
    for y in range(tetrion.height):
        for x in range(tetrion.width):
            pygame.draw.rect(
                screen,
                (64, 64, 64),
                pygame.Rect(position.x + (x + 6) * RECT_SIZE, position.y + y * RECT_SIZE, RECT_SIZE, RECT_SIZE),
                1
            )

    # render gray area right of the grid for the interface
    pygame.draw.rect(
        screen,
        (64, 64, 64),
        pygame.Rect(position.x + (tetrion.width + 6) * RECT_SIZE - 1, position.y, RECT_SIZE * 6,
                    tetrion.height * RECT_SIZE)
    )

    # render preview pieces
    preview_pieces = tetrion.get_preview_pieces()
    for i, tetromino_type in enumerate(preview_pieces):
        mino_positions = _tetromino_get_mino_positions(tetromino_type, Rotation.NORTH)
        for pos in mino_positions:
            render_mino(
                tetrion.width + pos.x + 7,
                pos.y + i * 3 + 1,
                COLORS[tetromino_type.value],
                GHOST_COLORS[tetromino_type.value],
            )

    matrix = tetrion.matrix()
    for y, row in enumerate(matrix.rows):
        for x, mino in enumerate(row):
            if mino == TetrominoType.EMPTY:
                continue
            render_mino(
                x + 6,
                y,
                COLORS[mino.value],
                GHOST_COLORS[mino.value],
            )

    ghost_tetromino = tetrion.try_get_ghost_tetromino()
    if ghost_tetromino is not None:
        render_tetromino(ghost_tetromino, Vec2(6, 0), GHOST_COLORS, GHOST_COLORS)

    active_tetromino = tetrion.try_get_active_tetromino()
    if active_tetromino is not None:
        render_tetromino(active_tetromino, Vec2(6, 0), COLORS, GHOST_COLORS)

    line_clear_delay_state = tetrion.get_line_clear_delay_state()
    if len(line_clear_delay_state.lines):
        brightness = int(255 * line_clear_delay_state.countdown / line_clear_delay_state.delay)
        for line in line_clear_delay_state.lines:
            pygame.draw.rect(
                screen,
                (brightness, brightness, brightness),
                pygame.Rect(
                    position.x + 6 * RECT_SIZE,
                    position.y + line * RECT_SIZE,
                    tetrion.width * RECT_SIZE,
                    RECT_SIZE,
                )
            )


class MessageType(Enum):
    HEARTBEAT = 0
    GRID_STATE = 1
    GAME_START = 2
    STATE_BROADCAST = 3


class MessageHeader(NamedTuple):
    type_: MessageType
    payload_size: int


class ClientEvents(NamedTuple):
    client_id: int
    events: list[Event]


class BroadcastMessage(NamedTuple):
    frame: int
    states_per_client: dict[int, list[set[Key]]]


class GameStartMessage(NamedTuple):
    client_id: int
    start_frame: int
    random_seed: int


pressed_keys: set[Key] = set()


def simulation_worker(
        target: socket.socket,
        start_time: float,
        running: Synchronized[bool],
        tetrion: Tetrion,
) -> None:
    print("entering main loop in keep_sending_heartbeats()")
    buffer: list[set[Key]] = []
    frame = 0
    while True:
        with running.lock() as is_running:
            if not is_running.get():
                break
        elapsed = time.monotonic() - start_time
        current_frame = int(elapsed / (1.0 / 60.0))
        while frame < current_frame:
            tetrion.simulate_next_frame(pressed_keys)
            buffer.append(pressed_keys.copy())
            if len(buffer) == 15:
                send_heartbeat_message(target, frame, buffer)
                buffer.clear()
            frame += 1
        time.sleep(1.0 / 180.0)
    print("main loop ended in keep_sending_heartbeats()")


message_queue: list[BroadcastMessage | GameStartMessage] = []


def keep_receiving(server_socket: socket.socket, running: Synchronized[bool]) -> None:
    buffer = bytearray()
    current_header: Optional[MessageHeader] = None
    print("entering main loop in keep_receiving()")
    while True:
        with running.lock() as is_running:
            if not is_running.get():
                break
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
                    message_queue.append(game_start_message)
                case MessageType.STATE_BROADCAST:
                    message_frame, num_clients = struct.unpack("!QB", buffer[:9])
                    buffer = buffer[9:]
                    states_per_client: dict[int, list[set[Key]]] = dict()
                    for _ in range(num_clients):
                        client_id, = struct.unpack("!B", buffer[:1])
                        buffer = buffer[1:]
                        states: list[set[Key]] = list()
                        for _ in range(15):
                            state, = struct.unpack("!B", buffer[:1])
                            buffer = buffer[1:]
                            states.append(set(key for key in Key if state & (1 << key.value) != 0))
                        assert len(states) == 15
                        assert client_id not in states_per_client
                        states_per_client[client_id] = states
                    broadcast_message = BroadcastMessage(message_frame, states_per_client)
                    message_queue.append(broadcast_message)
                case _:
                    raise Exception(f"invalid message type: {current_header.type_.name}")
            current_header = None

    print("main loop ended in keep_receiving()")


_LOBBY_URL = "http://127.0.0.1:5000"


class GameStartResult(NamedTuple):
    user: User
    lobby: Lobby
    gameserver_port: int


def login_and_create_or_join_lobby(connection: LobbyServerConnection) -> GameStartResult:
    with connection.get_lobby_list() as lobby_list:
        num_lobbies = len(lobby_list.lobbies)

    credentials = ("r00tifant", "sudo") if num_lobbies == 0 else ("coder2k", "secret")

    user = connection.authenticate_user(*credentials)

    if num_lobbies == 0:
        lobby = connection.create_lobby(user, "coder2k's lobby", 2)
        # todo: implement function that can retrieve the lobby info of a given lobby
        while True:
            with connection.get_lobby_list() as lobby_list:
                lobby_info = lobby_list.lobbies[0]
                lobby_details = connection.get_lobby_details(lobby_info, user)
            if MODE == Mode.TWO_PLAYERS:
                if len(lobby_details.client_infos) == 0:
                    logging.debug("second player not present yet...")
                    time.sleep(1.0)
                    continue
            gameserver_port = connection.start_lobby(user, lobby)
            if gameserver_port is not None:
                logging.debug("game started")
                return GameStartResult(user, lobby, gameserver_port)
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
        return GameStartResult(user, lobby, gameserver_port)


def main() -> None:
    with LobbyServerConnection("127.0.0.1", 5000) as connection:
        user, lobby, gameserver_port = login_and_create_or_join_lobby(connection)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as gameserver_socket:
            print("trying to connect to gameserver...")
            gameserver_socket.connect(("127.0.0.1", gameserver_port))
            gameserver_socket.setblocking(False)

            running = Synchronized(True)
            worker_thread = threading.Thread(target=keep_receiving, args=(gameserver_socket, running))
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
                size = (RECT_SIZE * (tetrion.width + 12) * 2, (RECT_SIZE + 2) * tetrion.height)
                screen = pygame.display.set_mode(size)

                simulation_step = 0

                clock = pygame.time.Clock()

                start_time = time.monotonic()

                heartbeat_thread = threading.Thread(
                    target=simulation_worker,
                    args=(gameserver_socket, start_time, running, tetrion),
                )
                heartbeat_thread.start()

                other_tetrion_key_states_buffer: list[set[Key]] = []

                while True:
                    with running.lock() as is_running:
                        if not is_running.get():
                            break

                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            with running.lock() as is_running:
                                is_running.set(False)
                        elif event.type == pygame.KEYDOWN:
                            if event.key == controls.QUIT:
                                with running.lock() as is_running:
                                    is_running.set(False)
                            elif event.key == controls.LEFT:
                                pressed_keys.add(Key.LEFT)
                            elif event.key == controls.RIGHT:
                                pressed_keys.add(Key.RIGHT)
                            elif event.key == controls.DOWN:
                                pressed_keys.add(Key.DOWN)
                            elif event.key == controls.ROTATE_COUNTER_CLOCKWISE:
                                pressed_keys.add(Key.ROTATE_CCW)
                            elif event.key == controls.ROTATE_CLOCKWISE:
                                pressed_keys.add(Key.ROTATE_CW)
                            elif event.key == controls.DROP:
                                pressed_keys.add(Key.DROP)
                            elif event.key == controls.HOLD:
                                pressed_keys.add(Key.HOLD)
                        elif event.type == pygame.KEYUP:
                            if event.key == controls.LEFT:
                                pressed_keys.remove(Key.LEFT)
                            elif event.key == controls.RIGHT:
                                pressed_keys.remove(Key.RIGHT)
                            elif event.key == controls.DOWN:
                                pressed_keys.remove(Key.DOWN)
                            elif event.key == controls.ROTATE_COUNTER_CLOCKWISE:
                                pressed_keys.remove(Key.ROTATE_CCW)
                            elif event.key == controls.ROTATE_CLOCKWISE:
                                pressed_keys.remove(Key.ROTATE_CW)
                            elif event.key == controls.DROP:
                                pressed_keys.remove(Key.DROP)
                            elif event.key == controls.HOLD:
                                pressed_keys.remove(Key.HOLD)

                    while len(message_queue) > 0:
                        broadcast_message = message_queue.pop(0)
                        assert isinstance(broadcast_message, BroadcastMessage)
                        assert len(broadcast_message.states_per_client) >= 1
                        if MODE == Mode.TWO_PLAYERS:
                            other_client_id = 1 if client_id == 0 else 0
                        else:
                            other_client_id = 0
                        if other_client_id in broadcast_message.states_per_client:
                            for state in broadcast_message.states_per_client[other_client_id]:
                                other_tetrion_key_states_buffer.append(state)

                    max_other_tetrion_frame = tetrion.get_next_frame() - 60
                    while (
                            len(other_tetrion_key_states_buffer) > 0
                            and other_tetrion.get_next_frame() <= max_other_tetrion_frame
                    ):
                        other_tetrion.simulate_next_frame(other_tetrion_key_states_buffer.pop(0))

                    screen.fill((0, 0, 0))

                    render_tetrion(screen, Vec2(0, 0), tetrion)
                    render_tetrion(screen, Vec2((tetrion.width + 12) * RECT_SIZE, 0), other_tetrion)

                    clock.tick()
                    fps = int(clock.get_fps())

                    game_font = pygame.font.Font(None, 30)
                    fps_counter = game_font.render(
                        f"{fps} FPS, simulation step {simulation_step}, {tetrion.get_next_frame() =}, "
                        + f"{other_tetrion.get_next_frame() =}, delta = {tetrion.get_next_frame() - other_tetrion.get_next_frame()}",
                        True,
                        (255, 255, 255))

                    screen.blit(fps_counter, (5, 5 + tetrion.height * RECT_SIZE))

                    pygame.display.flip()

                    elapsed = time.monotonic() - start_time
                    new_frame = int(elapsed / (1.0 / 60.0))

                    while simulation_step < new_frame - 1:
                        simulation_step += 1

            print("main loop ended")

            heartbeat_thread.join()
            worker_thread.join()

            gameserver_socket.close()

            pygame.quit()

        connection.destroy_lobby(user, lobby)
        user.destroy()


if __name__ == "__main__":
    main()
