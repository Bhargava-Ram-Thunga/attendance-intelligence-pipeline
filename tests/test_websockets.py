import pytest
import json
from channels.testing import WebsocketCommunicator
from notifications.consumers import NotificationConsumer
from django.conf import settings
import jwt

@pytest.mark.asyncio
async def test_websocket_connection_unauthorized():
    """
    Verify that the consumer rejects connections without a token.
    """
    communicator = WebsocketCommunicator(NotificationConsumer.as_asgi(), "/ws/notifications/")
    connected, response = await communicator.connect()

    assert not connected
    assert response == 4001 # Unauthorized code

@pytest.mark.asyncio
async def test_websocket_connection_authorized():
    """
    Verify that the consumer accepts connections with a valid JWT.
    """
    # Create a valid JWT token
    user_id = "test-user-uuid"
    token = jwt.encode({"user_id": user_id}, settings.JWT_SECRET, algorithm="HS256")

    # Connect with token in query string
    communicator = WebsocketCommunicator(
        NotificationConsumer.as_asgi(),
        f"/ws/notifications/?token={token}"
    )
    connected, response = await communicator.connect()

    assert connected
    await communicator.disconnect()

@pytest.mark.asyncio
async def test_websocket_connection_invalid_token():
    """
    Verify that the consumer rejects connections with an invalid token.
    """
    communicator = WebsocketCommunicator(
        NotificationConsumer.as_asgi(),
        "/ws/notifications/?token=invalid-token-123"
    )
    connected, response = await communicator.connect()

    assert not connected
    assert response == 4001
