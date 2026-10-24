// Точки подключения онбординга к остальному коду без циклических импортов: invite.js вызывает хук, register.js его задаёт.
export const hooks = {
  // после активации приглашения: { ctx, userId, maxId, startDate }
  onActivated: async () => {},
};
