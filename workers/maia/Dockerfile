FROM node:26.10.0-bookworm-slim@sha256:3ffc19ea878019d9e9ae8971732ad4a03cda44f167107173174b60ed7c65bed3
ENV NODE_ENV=production
WORKDIR /opt/maia
COPY artifact.tgz package-lock.json /tmp/
RUN tar -xzf /tmp/artifact.tgz --strip-components=1 -C /opt/maia && cp /tmp/package-lock.json /opt/maia/package-lock.json && npm ci --ignore-scripts --omit=dev --workspaces=false && rm /tmp/artifact.tgz /tmp/package-lock.json
USER node
ENTRYPOINT ["node", "/opt/maia/cli.mjs"]
CMD ["serve"]
