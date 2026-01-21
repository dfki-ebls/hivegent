import { createFileRoute, redirect } from '@tanstack/react-router';
import { createConversation } from '../lib/api';

export const Route = createFileRoute('/')({
  beforeLoad: async () => {
    const id = await createConversation();
    throw redirect({
      to: '/chat/$id',
      params: { id },
    });
  },
  component: () => null,
});
