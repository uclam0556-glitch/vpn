import { useEffect, useState } from 'react';
import axios from 'axios';
import { Users, CreditCard, Key, Copy, Plus, Clock, Trash2, Edit3, X, Zap } from 'lucide-react';

export default function ResellerDashboard({ profile, haptic }: any) {
  const [stats, setStats] = useState<any>(null);
  const [clients, setClients] = useState<any[]>([]);
  const [tariffs, setTariffs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const [isBuyModalOpen, setIsBuyModalOpen] = useState(false);
  const [buyLoading, setBuyLoading] = useState(false);
  const [buyForm, setBuyForm] = useState({ tariff_id: 0, note: '', client_name: '' });

  const [selectedClient, setSelectedClient] = useState<any>(null);
  const [editLimit, setEditLimit] = useState(1);
  const [actionLoading, setActionLoading] = useState(false);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const token = localStorage.getItem('portal_access_key') || '';
      const headers = { 'Authorization': `Bearer ${token}` };
      const [resStats, resClients, resTariffs] = await Promise.all([
        axios.get('/api/reseller/dashboard', { headers }),
        axios.get('/api/reseller/clients', { headers }),
        axios.get('/api/reseller/tariffs', { headers })
      ]);
      setStats(resStats.data);
      setClients(resClients.data);
      setTariffs(resTariffs.data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleBuy = async (e: React.FormEvent) => {
    e.preventDefault();
    setBuyLoading(true);
    try {
      const token = localStorage.getItem('portal_access_key') || '';
      const headers = { 'Authorization': `Bearer ${token}` };
      await axios.post('/api/reseller/keys/buy', buyForm, { headers });
      setIsBuyModalOpen(false);
      haptic('heavy');
      fetchData();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Ошибка при покупке');
    } finally {
      setBuyLoading(false);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    haptic('light');
  };

  const handleUpdateLimit = async () => {
    if (!selectedClient) return;
    setActionLoading(true);
    try {
      const token = localStorage.getItem('portal_access_key') || '';
      const headers = { 'Authorization': `Bearer ${token}` };
      await axios.put(`/api/reseller/clients/${selectedClient.remnawave_uuid}`, { devices_limit: editLimit }, { headers });
      haptic('medium');
      setSelectedClient({ ...selectedClient, device_limit: editLimit });
      fetchData();
    } catch {
      alert('Ошибка при сохранении лимита');
    } finally {
      setActionLoading(false);
    }
  };

  const handleRevoke = async () => {
    if (!selectedClient) return;
    if (!confirm('Вы уверены, что хотите безвозвратно удалить этот ключ?')) return;
    setActionLoading(true);
    try {
      const token = localStorage.getItem('portal_access_key') || '';
      const headers = { 'Authorization': `Bearer ${token}` };
      await axios.delete(`/api/reseller/clients/${selectedClient.remnawave_uuid}`, { headers });
      haptic('heavy');
      setSelectedClient(null);
      fetchData();
    } catch {
      alert('Ошибка при удалении ключа');
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) return <div className="text-center py-20 animate-pulse">Загрузка панели...</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-white to-white/60">
            Добро пожаловать, {profile.name}
          </h1>
          <p className="text-muted mt-1 flex items-center gap-2">
            Уровень партнера: <span className="px-2 py-0.5 rounded-full bg-accent/20 text-accent text-xs font-bold">{profile.level}</span>
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="glass-card rounded-[24px] p-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 w-32 h-32 bg-accent/10 rounded-bl-[100px] -z-10 group-hover:bg-accent/20 transition-colors" />
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-muted font-medium flex items-center gap-2"><CreditCard className="w-5 h-5" /> Ваш Баланс</h3>
            <button className="w-10 h-10 rounded-full bg-white/10 flex items-center justify-center hover:bg-white/20 transition-colors">
              <Plus className="w-5 h-5" />
            </button>
          </div>
          <div className="text-4xl font-bold font-mono tracking-tight">{stats?.balance?.toFixed(2)} ₽</div>
        </div>

        <div className="glass-card rounded-[24px] p-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 w-32 h-32 bg-purple-500/10 rounded-bl-[100px] -z-10 group-hover:bg-purple-500/20 transition-colors" />
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-muted font-medium flex items-center gap-2"><Users className="w-5 h-5" /> Всего Клиентов</h3>
          </div>
          <div className="text-4xl font-bold font-mono tracking-tight">{stats?.clients_count}</div>
        </div>
      </div>

      {/* Tariffs Section */}
      <div className="mt-8">
        <h2 className="text-2xl font-bold mb-6 flex items-center gap-2"><Zap className="w-6 h-6 text-yellow-400" /> Выпуск нового ключа (Тарифы)</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {tariffs.map(t => (
            <div key={t.id} className="glass-card p-5 rounded-[20px] flex flex-col hover:border-accent/50 transition-colors cursor-pointer group" onClick={() => { setBuyForm({ ...buyForm, tariff_id: t.id }); setIsBuyModalOpen(true); }}>
              <div className="text-xl font-bold mb-1">{t.name}</div>
              <div className="text-sm text-muted mb-4">{t.duration_days} дней • {t.device_limit} устр.</div>
              <div className="mt-auto flex items-center justify-between">
                <span className="text-xl font-bold text-accent">{t.price_rub} ₽</span>
                <div className="w-8 h-8 rounded-full bg-accent/20 flex items-center justify-center group-hover:bg-accent text-white transition-colors">
                  <Plus className="w-4 h-4" />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Clients List */}
      <div className="mt-12">
        <h2 className="text-2xl font-bold mb-6">Ваши Клиенты (Ключи)</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {clients.map(c => (
            <div key={c.id} onClick={() => { setSelectedClient(c); setEditLimit(c.device_limit || 1); haptic('light'); }} className="glass-card p-5 rounded-[20px] hover:bg-white/5 transition-colors cursor-pointer border border-transparent hover:border-white/10">
              <div className="flex justify-between items-start mb-3">
                <div className="font-bold text-lg">{c.name || 'Без имени'}</div>
                <div className={`px-2 py-1 rounded-full text-xs font-bold ${c.sub_status === 'active' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                  {c.sub_status === 'active' ? 'Активен' : 'Отключен'}
                </div>
              </div>
              <div className="text-sm text-muted mb-1 flex items-center gap-2">
                <Clock className="w-4 h-4" /> {c.expires_at ? new Date(c.expires_at).toLocaleDateString() : 'Бесконечно'}
              </div>
              <div className="text-sm text-muted mb-3 flex items-center gap-2">
                <Key className="w-4 h-4" /> Лимит устройств: {c.device_limit || 0}
              </div>
            </div>
          ))}
          {clients.length === 0 && <div className="text-muted col-span-full">У вас пока нет клиентов. Купите тариф выше, чтобы сгенерировать первый ключ!</div>}
        </div>
      </div>

      {/* Client Detail Modal */}
      {selectedClient && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setSelectedClient(null)}></div>
          <div className="glass-card w-full max-w-lg rounded-[32px] p-8 relative z-10 animate-in zoom-in-95 duration-200 border border-white/10 shadow-2xl">
            <button onClick={() => setSelectedClient(null)} className="absolute top-6 right-6 p-2 rounded-full hover:bg-white/10 transition-colors"><X className="w-5 h-5" /></button>

            <h2 className="text-2xl font-bold mb-2">{selectedClient.name || 'Без имени'}</h2>
            <div className={`inline-block px-3 py-1 rounded-full text-sm font-bold mb-6 ${selectedClient.sub_status === 'active' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
              Статус: {selectedClient.sub_status}
            </div>

            <div className="space-y-6">
              <div>
                <label className="text-sm text-muted block mb-2">Ссылка на подписку</label>
                <div className="flex gap-2">
                  <input readOnly value={selectedClient.sub_url || 'Нет ссылки'} className="flex-1 bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white/70" />
                  <button onClick={() => copyToClipboard(selectedClient.sub_url)} className="px-4 bg-white/10 rounded-xl hover:bg-white/20 transition-colors"><Copy className="w-5 h-5" /></button>
                </div>
              </div>

              <div className="bg-white/5 rounded-2xl p-5 border border-white/10">
                <div className="flex items-center justify-between mb-4">
                  <div className="font-medium flex items-center gap-2"><Edit3 className="w-5 h-5" /> Устройства (Лимит)</div>
                  <div className="flex items-center gap-3">
                    <button onClick={() => setEditLimit(Math.max(1, editLimit - 1))} className="w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center">-</button>
                    <span className="font-bold text-lg w-4 text-center">{editLimit}</span>
                    <button onClick={() => setEditLimit(editLimit + 1)} className="w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center">+</button>
                  </div>
                </div>
                <button onClick={handleUpdateLimit} disabled={actionLoading || editLimit === selectedClient.device_limit} className="w-full bg-accent/20 text-accent hover:bg-accent/30 py-3 rounded-xl font-medium transition-colors disabled:opacity-50">
                  Сохранить лимит
                </button>
              </div>

              <div className="pt-4 border-t border-white/10">
                <button onClick={handleRevoke} disabled={actionLoading} className="w-full flex items-center justify-center gap-2 bg-red-500/20 text-red-400 hover:bg-red-500/30 py-4 rounded-xl font-bold transition-colors">
                  <Trash2 className="w-5 h-5" /> Отозвать и удалить ключ
                </button>
                <p className="text-xs text-muted text-center mt-3">Удаление ключа безвозвратно отключит клиента от VPN.</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Buy Modal */}
      {isBuyModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setIsBuyModalOpen(false)}></div>
          <div className="glass-card w-full max-w-md rounded-[32px] p-8 relative z-10 animate-in zoom-in-95 duration-200 border border-white/10 shadow-2xl">
            <button onClick={() => setIsBuyModalOpen(false)} className="absolute top-6 right-6 p-2 rounded-full hover:bg-white/10 transition-colors"><X className="w-5 h-5" /></button>
            <h2 className="text-2xl font-bold mb-6">Покупка ключа</h2>
            <form onSubmit={handleBuy} className="space-y-4">
              <div>
                <label className="text-sm text-muted block mb-2">Имя клиента (для себя)</label>
                <input
                  required
                  type="text"
                  placeholder="Иван Иванов"
                  value={buyForm.client_name}
                  onChange={e => setBuyForm({ ...buyForm, client_name: e.target.value })}
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-4 focus:border-accent transition-colors outline-none"
                />
              </div>
              <div>
                <label className="text-sm text-muted block mb-2">Заметки (опционально)</label>
                <input
                  type="text"
                  placeholder="Оплатил наличными..."
                  value={buyForm.note}
                  onChange={e => setBuyForm({ ...buyForm, note: e.target.value })}
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-4 focus:border-accent transition-colors outline-none"
                />
              </div>
              <button
                type="submit"
                disabled={buyLoading}
                className="w-full mt-4 bg-accent text-white font-bold py-4 rounded-xl shadow-[0_4px_20px_rgba(var(--accent),0.4)] hover:opacity-90 active:scale-95 transition-all disabled:opacity-50"
              >
                {buyLoading ? 'Создание...' : 'Подтвердить и списать баланс'}
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
