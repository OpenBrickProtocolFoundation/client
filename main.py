import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple
from typing import Optional

import pygame
import requests
from dataclasses_jsonschema import JsonSchemaMixin

from tetrion import Event
from tetrion import EventType
from tetrion import Key
from tetrion import Tetrion
from tetrion import Vec2

COLORS = [(0, 0, 0),
          (0, 240, 240),
          (0, 0, 240),
          (240, 160, 0),
          (240, 240, 0),
          (0, 240, 0),
          (160, 0, 240),
          (240, 0, 0)]

RECT_SIZE = 30

done = False


def send_event_buffer(target: socket.socket, events: list[Event], frame: int) -> None:
    print(f"sending event buffer: {frame=}, {events=}")
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

    active_tetromino = tetrion.try_get_active_tetromino()
    if active_tetromino is not None:
        for pos in active_tetromino.mino_positions:
            x, y = pos.x, pos.y
            pygame.draw.rect(screen, COLORS[active_tetromino.type.value],
                             pygame.Rect(position.x + x * RECT_SIZE, position.y + y * RECT_SIZE, RECT_SIZE, RECT_SIZE))


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
        try:
            # print("trying to receive message")
            data = server_socket.recv(4096)
            print("received message")
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
            print(f"received message of type {current_header.type_.name}")

            match current_header.type_:
                case MessageType.GAME_START:
                    client_id, start_frame, random_seed = struct.unpack("!BQQ", buffer[:17])
                    buffer = buffer[17:]
                    game_start_message = GameStartMessage(client_id, start_frame, random_seed)
                    print(game_start_message)
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
                    print(broadcast_message)
                    message_queue.append(broadcast_message)
                case _:
                    raise Exception(f"invalid message type: {current_header.type_.name}")
            current_header = None

    print("main loop ended in keep_receiving()")


_LOBBY_URL = "http://127.0.0.1:5000"


@dataclass
class PlayerInfo(JsonSchemaMixin):
    id: str
    name: str


@dataclass
class LobbyInfo(JsonSchemaMixin):
    id: str
    name: str
    size: int
    num_players_in_lobby: int
    host_info: PlayerInfo


@dataclass
class LobbyListResponse(JsonSchemaMixin):
    lobbies: list[LobbyInfo]


def fetch_lobbies() -> LobbyListResponse:
    response = requests.get(f"{_LOBBY_URL}/lobbies")
    assert response.status_code == 200
    return LobbyListResponse.from_dict(response.json())


def login(username: str, password: str) -> str:
    @dataclass
    class Credentials(JsonSchemaMixin):
        username: str
        password: str

    credentials = Credentials(username, password)
    print("sending post request")
    response = requests.post(f"{_LOBBY_URL}/login", json=credentials.to_dict())
    print("received response")
    assert response.status_code == 200

    @dataclass
    class LoginResponse(JsonSchemaMixin):
        jwt: str

    login_response = LoginResponse.from_dict(response.json())
    return login_response.jwt


def create_lobby(jwt: str, name: str, size: int) -> str:
    @dataclass
    class CreateLobbyRequest(JsonSchemaMixin):
        name: str
        size: int

    headers = {"Authorization": f"Bearer {jwt}"}
    payload = CreateLobbyRequest(name, size)
    response = requests.post(f"{_LOBBY_URL}/lobbies", json=payload.to_dict(), headers=headers)
    assert response.status_code == 201

    @dataclass
    class LobbyCreationResponse(JsonSchemaMixin):
        id: str

    return LobbyCreationResponse.from_dict(response.json()).id


def start_game(jwt: str, lobby_id: str) -> None:
    headers = {"Authorization": f"Bearer {jwt}"}
    response = requests.post(f"{_LOBBY_URL}/lobbies/{lobby_id}/start", headers=headers)
    assert response.status_code == 204


@dataclass
class LobbyResponse(JsonSchemaMixin):
    name: str
    size: int
    host_info: PlayerInfo
    player_infos: list[PlayerInfo]
    gameserver_port: Optional[int]


def get_lobby_details(jwt: str, lobby_id: str) -> LobbyResponse:
    headers = {"Authorization": f"Bearer {jwt}"}
    response = requests.get(f"{_LOBBY_URL}/lobbies/{lobby_id}", headers=headers)
    assert response.status_code == 200
    return LobbyResponse.from_dict(response.json())


def main() -> None:
    global done
    gameserver_port = int(input("Enter the port of the gameserver: "))

    with (socket.socket(socket.AF_INET, socket.SOCK_STREAM) as gameserver_socket,
          Tetrion() as tetrion,
          Tetrion() as other_tetrion
          ):
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

        # todo: use values of game_start_message
        client_id = game_start_message.client_id

        pygame.init()

        size = (RECT_SIZE * tetrion.width * 2, (RECT_SIZE + 2) * tetrion.height)
        screen = pygame.display.set_mode(size)

        clock = pygame.time.Clock()

        frame = 0

        event_buffer: list[Event] = []

        other_client_frame: Optional[int] = None

        while not done:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    done = True
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        done = True
                    elif event.key == pygame.K_a:
                        input_event = Event(key=Key.LEFT, type=EventType.PRESSED, frame=frame)
                        tetrion.enqueue_event(input_event)
                        event_buffer.append(input_event)
                    elif event.key == pygame.K_d:
                        input_event = Event(key=Key.RIGHT, type=EventType.PRESSED, frame=frame)
                        tetrion.enqueue_event(input_event)
                        event_buffer.append(input_event)
                    elif event.key == pygame.K_SPACE:
                        input_event = Event(key=Key.DROP, type=EventType.PRESSED, frame=frame)
                        tetrion.enqueue_event(input_event)
                        event_buffer.append(input_event)

            tetrion.simulate_up_until(frame)

            while len(message_queue) > 0:
                broadcast_message = message_queue.pop(0)
                assert isinstance(broadcast_message, BroadcastMessage)
                assert len(broadcast_message.events_per_client) == 2
                other_client_frame = broadcast_message.frame
                for input_event in broadcast_message.events_per_client[1 if client_id == 0 else 0].events:
                    other_tetrion.enqueue_event(input_event)

            if frame > 30 and other_client_frame is not None:
                other_tetrion.simulate_up_until(min(frame - 30, other_client_frame))

            screen.fill((100, 100, 100))

            render_tetrion(screen, Vec2(0, 0), tetrion)
            render_tetrion(screen, Vec2(tetrion.width * RECT_SIZE, 0), other_tetrion)

            game_font = pygame.font.Font(None, 30)
            clock.tick(60)
            fps_counter = game_font.render(f"FPS: {int(clock.get_fps())} (frame {frame})", True, (255, 255, 255))

            screen.blit(fps_counter, (5, 5 + tetrion.height * RECT_SIZE))

            pygame.display.flip()

            if frame % 15 == 0:
                send_event_buffer(gameserver_socket, event_buffer, frame)
                event_buffer.clear()

            frame += 1

        print("main loop ended")

        worker_thread.join()

        gameserver_socket.close()

        pygame.quit()


if __name__ == "__main__":
    main()
