import socket
import struct
import threading
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


def send_event(target: socket.socket, event: Event) -> None:
    data = struct.pack("!BQBBQ", 0, 10, event.key.value, event.type.value, event.frame)
    print(f"sending data {data!r}")
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
    INPUT_EVENT = 0


class MessageHeader(NamedTuple):
    type_: MessageType
    payload_size: int


event_queue: list[Event] = []


def keep_receiving(server_socket: socket.socket) -> None:
    buffer = bytearray()
    current_header: Optional[MessageHeader] = None
    while True:
        data = server_socket.recv(4096)
        buffer.extend(data)
        if current_header is None and len(buffer) >= 9:
            type_, payload_size = struct.unpack("!BQ", buffer[:9])
            buffer = buffer[9:]
            current_header = MessageHeader(type_=MessageType(type_), payload_size=payload_size)
        if current_header is not None and len(buffer) >= current_header.payload_size:
            assert current_header.type_ == MessageType.INPUT_EVENT
            key, event_type, frame = struct.unpack("!BBQ", buffer[:current_header.payload_size])
            buffer = buffer[current_header.payload_size:]
            event = Event(Key(key), EventType(event_type), frame)
            event_queue.append(event)
            current_header = None
            print(f"Received event: {event}")


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
    jwt = login("coder2k", "secret")
    print(f"{jwt = }")

    lobbies_response = fetch_lobbies()
    lobby_id: Optional[str] = None
    for lobby in lobbies_response.lobbies:
        if lobby.host_info.name == "coder2k":
            lobby_id = lobby.id
            print(f"already inside existing lobby: {lobby_id}")
            break

    if lobby_id is None:
        lobby_id = create_lobby(jwt, "coder2k's lobby", 4)
        print(f"Lobby created: {lobby_id}")

    assert lobby_id is not None

    start_game(jwt, lobby_id)

    gameserver_port: Optional[int] = None
    while True:
        lobby_details = get_lobby_details(jwt, lobby_id)
        if lobby_details.gameserver_port is not None:
            gameserver_port = lobby_details.gameserver_port
            break

    print(f"{gameserver_port = }")

    frame = 0

    with (socket.socket(socket.AF_INET, socket.SOCK_STREAM) as gameserver_socket,
          Tetrion() as tetrion,
          Tetrion() as other_tetrion
          ):
        print("trying to connect to gameserver...")
        gameserver_socket.connect(("127.0.0.1", gameserver_port))
        worker_thread = threading.Thread(target=keep_receiving, args=(gameserver_socket,))
        worker_thread.start()
        print("connected to gameserver")

        pygame.init()

        size = (RECT_SIZE * tetrion.width * 2, (RECT_SIZE + 2) * tetrion.height)
        screen = pygame.display.set_mode(size)

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
                        input_event = Event(key=Key.LEFT, type=EventType.PRESSED, frame=frame)
                        tetrion.enqueue_event(input_event)
                        send_event(gameserver_socket, input_event)
                    elif event.key == pygame.K_d:
                        input_event = Event(key=Key.RIGHT, type=EventType.PRESSED, frame=frame)
                        tetrion.enqueue_event(input_event)
                        send_event(gameserver_socket, input_event)
                    elif event.key == pygame.K_SPACE:
                        input_event = Event(key=Key.DROP, type=EventType.PRESSED, frame=frame)
                        tetrion.enqueue_event(input_event)
                        send_event(gameserver_socket, input_event)

            tetrion.simulate_up_until(frame)

            while len(event_queue) > 0:
                input_event = event_queue.pop(0)
                other_tetrion.enqueue_event(input_event)
                other_tetrion.simulate_up_until(input_event.frame)

            screen.fill((100, 100, 100))

            render_tetrion(screen, Vec2(0, 0), tetrion)
            render_tetrion(screen, Vec2(tetrion.width * RECT_SIZE, 0), other_tetrion)

            game_font = pygame.font.Font(None, 30)
            clock.tick(60)
            fps_counter = game_font.render(f"FPS: {int(clock.get_fps())} (frame {frame})", True, (255, 255, 255))

            screen.blit(fps_counter, (5, 5 + tetrion.height * RECT_SIZE))

            pygame.display.flip()
            frame += 1

        pygame.quit()


if __name__ == "__main__":
    main()
