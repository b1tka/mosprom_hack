// Выполняется один раз при создании тома mongo_data (как db/init для Postgres).
// Сервисы подключаются отдельным пользователем с правами только на базу приложения, не root-ом.
const dbName = process.env.MONGO_INITDB_DATABASE;
db.getSiblingDB(dbName).createUser({
  user: process.env.MONGO_APP_USER,
  pwd: process.env.MONGO_APP_PASSWORD,
  roles: [{ role: 'readWrite', db: dbName }],
});
