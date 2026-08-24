FROM dailyco/pipecat-base:latest

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Copy the application code.
#
# bot.py alone is not enough: it imports the `ordering` package and reads
# prompts/system.txt at startup, so a container built without these starts and
# then dies on the first import. The menu and the 86'd list travel with the
# image, which also means redeploying is how a menu correction ships.
COPY ./bot.py bot.py
COPY ./ordering ./ordering
COPY ./prompts ./prompts
