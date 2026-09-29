FROM alpine:3.21

# Install bash, aws-cli, iproute2 (provides 'ss'), and postgresql-client
RUN apk add --no-cache bash curl aws-cli iproute2 postgresql-client

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
