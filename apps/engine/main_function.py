import functions_framework
from fastapi import Request as FastAPIRequest
from main import app
import asyncio

@functions_framework.http
def healops_handler(request):
    """
    Cloud Functions entry point for HealOps Engine.
    Wraps the FastAPI application to work with Cloud Functions.
    """
    # Import here to avoid issues with Cloud Functions cold starts
    from asgiref.wsgi import WsgiToAsgi
    from werkzeug.wrappers import Response
    
    # Create an ASGI application from FastAPI
    asgi_app = app
    
    # Handle the request
    import asyncio
    from io import BytesIO
    
    # Convert Cloud Functions request to ASGI format
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': request.method,
        'scheme': 'https',
        'path': request.path,
        'query_string': request.query_string,
        'headers': [(k.lower().encode(), v.encode()) for k, v in request.headers.items()],
        'server': (request.host.split(':')[0], int(request.host.split(':')[1]) if ':' in request.host else 443),
    }
    
    # Response container
    response_started = False
    status_code = 200
    response_headers = []
    body_parts = []
    
    async def receive():
        return {
            'type': 'http.request',
            'body': request.get_data(),
            'more_body': False,
        }
    
    async def send(message):
        nonlocal response_started, status_code, response_headers, body_parts
        
        if message['type'] == 'http.response.start':
            response_started = True
            status_code = message['status']
            response_headers = message.get('headers', [])
        elif message['type'] == 'http.response.body':
            body_parts.append(message.get('body', b''))
    
    # Run the ASGI app
    async def run_asgi():
        await asgi_app(scope, receive, send)
    
    asyncio.run(run_asgi())
    
    # Build response
    response_body = b''.join(body_parts)
    headers = {k.decode(): v.decode() for k, v in response_headers}
    
    return Response(
        response_body,
        status=status_code,
        headers=headers
    )
