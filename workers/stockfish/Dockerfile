FROM node:26.10.0-bookworm-slim@sha256:3ffc19ea878019d9e9ae8971732ad4a03cda44f167107173174b60ed7c65bed3
ENV NODE_ENV=production
WORKDIR /opt/stockfish
COPY artifact.tgz /tmp/artifact.tgz
RUN tar -xzf /tmp/artifact.tgz --strip-components=1 -C /opt/stockfish && npm ci --ignore-scripts --omit=dev --workspaces=false && rm /tmp/artifact.tgz
USER node
ENTRYPOINT ["node", "/opt/stockfish/cli.mjs"]
CMD ["serve"]
