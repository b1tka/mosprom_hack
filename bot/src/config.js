const required = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`Missing env var ${name}`);
  return value;
};

export const config = {
  botToken: required('BOT_TOKEN'),
  databaseUrl: process.env.DATABASE_URL ?? 'postgres://onboarding:change_me@localhost:5432/onboarding',
  redisUrl: process.env.REDIS_URL ?? 'redis://localhost:6380',
  s3: {
    endpoint: process.env.S3_ENDPOINT ?? 'http://localhost:9000',
    accessKey: process.env.S3_ACCESS_KEY ?? 'minioadmin',
    secretKey: process.env.S3_SECRET_KEY ?? 'change_me_too',
    bucket: process.env.S3_BUCKET ?? 'onboarding-files',
  },
  kafka: {
    brokers: (process.env.KAFKA_BROKERS ?? 'localhost:29092').split(','),
    requestsTopic: process.env.KAFKA_TOPIC_REQUESTS ?? 'ml.requests',
    responsesTopic: process.env.KAFKA_TOPIC_RESPONSES ?? 'ml.responses',
    groupId: process.env.KAFKA_GROUP_ID ?? 'onboarding-bot',
  },
};
