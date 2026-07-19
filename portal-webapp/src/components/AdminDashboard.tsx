import { useEffect, useState } from 'react';
import axios from 'axios';
import { Shield, Plus, Activity, ChevronRight, X, Clock, Key, Copy, Trash2, Edit3, Settings } from 'lucide-react';

export default function AdminDashboard({ haptic }: any) {
  const [resellers, setResellers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const [isTopupModalOpen, setIsTopupModalOpen] = useState(false);
  const [selectedReseller, setSelectedReseller] = useState<any>(null);
  const [topupAmount, setTopupAmount] = useState('');
  const [topupLoading, setTopupLoading] = useState(false);

  // Detail View State
  const [detailReseller, setDetailReseller] = useState<any>(null);
  const [resellerClients, setResellerClients] = useState<any[]>([]);
  const [clientsLoading, setClientsLoading] = useState(false);

  // Edit Client State
  const [editClient, setEditClient] = useState<any>(null);
  const [editLimit, setEditLimit] = useState(1);
  const [actionLoading, setActionLoading] = useState(false);

  useEffect(() => {
    fetchResellers();
  }, []);

  const fetchResellers = async () => {
    try {
      const token = localStorage.getItem('portal_access_key') || '';
      const headers = { 'Authorization': `Bearer ${token}` };
      const res = await axios.get('/api/admin/resellers', { headers });
      setResellers(res.data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const fetchResellerClients = async (resellerId: number) => {
    setClientsLoading(true);
    try {
      const token = localStorage.getItem('portal_access_key') || '';
      const headers = { 'Authorization': `Bearer ${token}` };
      const res = await axios.get(`/api/admin/resellers/${resellerId}/clients`, { headers });
      setResellerClients(res.data);
    } catch (e) {
      console.error(e);
    } finally {
      setClientsLoading(false);
    }
  };

  const handleTopup = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!topupAmount || isNaN(Number(topupAmount))) return;
    setTopupLoading(true);
    try {
      const token = localStorage.getItem('portal_access_key') || '';
      const headers = { 'Authorization': `Bearer ${token}` };
      await axios.post(`/api/admin/resellers/${selectedReseller.id}/topup`, { amount: Number(topupAmount) }, { headers });
      setIsTopupModalOpen(false);
      setTopupAmount('');
      haptic('heavy');
      fetchResellers();
    } catch {
      alert('Ошибка при пополнении');
    } finally {
      setTopupLoading(false);
    }
  };

  const openDetail = (r: any) => {
    setDetailReseller(r);
    fetchResellerClients(r.id);
    haptic('light');
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    haptic('light');
  };

  const handleUpdateLimit = async () => {
    if (!editClient) return;
    setActionLoading(true);
    try {
      const token = localStorage.getItem('portal_access_key') || '';
      const headers = { 'Authorization': `Bearer ${token}` };
      await axios.put(`/api/reseller/clients/${editClient.remnawave_uuid}`, { devices_limit: editLimit }, { headers });
      haptic('medium');
      setEditClient({ ...editClient, device_limit: editLimit });
      // Update in list
      setResellerClients(prev => prev.map(c => c.id === editClient.id ? { ...c, device_limit: editLimit } : c));
    } catch {
      alert('Ошибка при сохранении лимита');
    } finally {
      setActionLoading(false);
    }
  };

  const handleRevoke = async () => {
    if (!editClient) return;
    if (!confirm('Вы уверены, что хотите безвозвратно удалить этот ключ?')) return;
    setActionLoading(true);
    try {
      const token = localStorage.getItem('portal_access_key') || '';
      const headers = { 'Authorization': `Bearer ${token}` };
      await axios.delete(`/api/reseller/clients/${editClient.remnawave_uuid}`, { headers });
      haptic('heavy');
      setResellerClients(prev => prev.filter(c => c.id !== editClient.id));
      setEditClient(null);
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
          <h1 className="text-3xl font-bold text-red-500 drop-shadow-[0_0_15px_rgba(239,68,68,0.5)]">
            Super Admin
          </h1>
          <p className="text-muted mt-1">Управление всеми реселлерами</p>
        </div>
        <div className="w-12 h-12 rounded-full bg-red-500/20 flex items-center justify-center border border-red-500/30">
          <Shield className="w-6 h-6 text-red-400" />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {resellers.map(r => (
          <div key={r.id} className="glass-card p-6 rounded-[24px] border border-transparent hover:border-white/10 transition-colors group">
            <div className="flex justify-between items-start mb-4">
              <div>
                <div className="text-lg font-bold">{r.name}</div>
                <div className="text-xs text-muted">ID: {r.id} | TG: {r.telegram_id}</div>
              </div>
              <div className="px-3 py-1 rounded-full bg-white/10 text-xs font-bold text-white">
                Ур. {r.level}
              </div>
            </div>

            <div className="text-3xl font-mono font-bold mb-6 tracking-tight text-green-400">
              {r.balance} ₽
            </div>

            <div className="flex gap-2 mt-auto">
              <button
                onClick={() => { setSelectedReseller(r); setIsTopupModalOpen(true); haptic('light'); }}
                className="flex-1 bg-white text-black py-3 rounded-xl font-bold flex items-center justify-center gap-2 hover:opacity-90 active:scale-95 transition-all"
              >
                <Plus className="w-4 h-4" /> Баланс
              </button>
              <button
                onClick={() => openDetail(r)}
                className="flex-1 bg-white/10 text-white py-3 rounded-xl font-bold flex items-center justify-center gap-2 hover:bg-white/20 active:scale-95 transition-all"
              >
                Ключи <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        ))}
      </div>

      {resellers.length === 0 && (
        <div className="glass-card p-10 text-center rounded-[32px] border border-white/5">
          <Activity className="w-12 h-12 text-muted mx-auto mb-4 opacity-50" />
          <p className="text-muted">Реселлеров пока нет.</p>
        </div>
      )}

      {/* Reseller Detail Drawer/Modal */}
      {detailReseller && (
        <div className="fixed inset-0 z-40 flex justify-end">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity" onClick={() => setDetailReseller(null)}></div>
          <div className="w-full md:w-[600px] bg-[#0a0a0a] h-full relative z-50 border-l border-white/10 shadow-2xl flex flex-col animate-in slide-in-from-right duration-300">
            <div className="p-6 border-b border-white/10 flex items-center justify-between bg-white/5 backdrop-blur-md">
              <div>
                <h2 className="text-xl font-bold">Ключи: {detailReseller.name}</h2>
                <p className="text-sm text-muted">Баланс: {detailReseller.balance} ₽</p>
              </div>
              <button onClick={() => setDetailReseller(null)} className="p-2 rounded-full hover:bg-white/10 transition-colors"><X className="w-5 h-5" /></button>
            </div>

            <div className="flex-1 overflow-y-auto p-6">
              {clientsLoading ? (
                <div className="text-center text-muted py-10 animate-pulse">Загрузка ключей...</div>
              ) : (
                <div className="space-y-4">
                  {resellerClients.map(c => (
                    <div key={c.id} className="glass-card p-4 rounded-[20px] flex justify-between items-center group">
                      <div>
                        <div className="font-bold">{c.name || 'Без имени'}</div>
                        <div className="text-sm text-muted flex items-center gap-2 mt-1">
                          <Clock className="w-3 h-3" /> {c.expires_at ? new Date(c.expires_at).toLocaleDateString() : 'Бесконечно'}
                          <span className="mx-1">•</span>
                          <Key className="w-3 h-3" /> Устр: {c.device_limit || 0}
                        </div>
                      </div>
                      <button
                        onClick={() => { setEditClient(c); setEditLimit(c.device_limit || 1); haptic('light'); }}
                        className="w-10 h-10 rounded-full bg-white/5 flex items-center justify-center hover:bg-white/20 transition-colors"
                      >
                        <Settings className="w-5 h-5 text-accent" />
                      </button>
                    </div>
                  ))}
                  {resellerClients.length === 0 && <div className="text-center text-muted py-10">Нет проданных ключей</div>}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Edit Client Modal (on top of Drawer) */}
      {editClient && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setEditClient(null)}></div>
          <div className="glass-card w-full max-w-lg rounded-[32px] p-8 relative z-[70] animate-in zoom-in-95 duration-200 border border-white/10 shadow-2xl">
            <button onClick={() => setEditClient(null)} className="absolute top-6 right-6 p-2 rounded-full hover:bg-white/10 transition-colors"><X className="w-5 h-5" /></button>

            <h2 className="text-2xl font-bold mb-2">{editClient.name || 'Редактирование'}</h2>
            <div className={`inline-block px-3 py-1 rounded-full text-sm font-bold mb-6 ${editClient.sub_status === 'active' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
              Статус: {editClient.sub_status}
            </div>

            <div className="space-y-6">
              <div>
                <label className="text-sm text-muted block mb-2">Ссылка на подписку</label>
                <div className="flex gap-2">
                  <input readOnly value={editClient.sub_url || 'Нет ссылки'} className="flex-1 bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white/70" />
                  <button onClick={() => copyToClipboard(editClient.sub_url)} className="px-4 bg-white/10 rounded-xl hover:bg-white/20 transition-colors"><Copy className="w-5 h-5" /></button>
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
                <button onClick={handleUpdateLimit} disabled={actionLoading || editLimit === editClient.device_limit} className="w-full bg-accent/20 text-accent hover:bg-accent/30 py-3 rounded-xl font-medium transition-colors disabled:opacity-50">
                  Сохранить лимит
                </button>
              </div>

              <div className="pt-4 border-t border-white/10">
                <button onClick={handleRevoke} disabled={actionLoading} className="w-full flex items-center justify-center gap-2 bg-red-500/20 text-red-400 hover:bg-red-500/30 py-4 rounded-xl font-bold transition-colors">
                  <Trash2 className="w-5 h-5" /> Отозвать и удалить ключ
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Topup Modal */}
      {isTopupModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setIsTopupModalOpen(false)}></div>
          <div className="glass-card w-full max-w-md rounded-[32px] p-8 relative z-10 animate-in zoom-in-95 duration-200 border border-white/10 shadow-2xl">
            <h2 className="text-2xl font-bold mb-2">Начислить баланс</h2>
            <p className="text-muted mb-6">Реселлер: <span className="text-white font-medium">{selectedReseller?.name}</span></p>
            <form onSubmit={handleTopup}>
              <div className="relative mb-6">
                <input
                  type="number"
                  placeholder="Сумма (₽)"
                  value={topupAmount}
                  onChange={e => setTopupAmount(e.target.value)}
                  className="w-full bg-black/40 border border-white/10 rounded-2xl py-4 px-6 text-2xl font-bold text-center text-white placeholder-muted focus:outline-none focus:border-red-500 transition-colors"
                  autoFocus
                />
              </div>
              <div className="flex gap-3">
                <button type="button" onClick={() => setIsTopupModalOpen(false)} className="flex-1 py-4 rounded-xl font-bold bg-white/10 hover:bg-white/20 transition-colors">
                  Отмена
                </button>
                <button type="submit" disabled={topupLoading} className="flex-1 py-4 rounded-xl font-bold bg-red-500 hover:bg-red-600 shadow-[0_4px_20px_rgba(239,68,68,0.4)] transition-all disabled:opacity-50 text-white">
                  {topupLoading ? 'Загрузка...' : 'Пополнить'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
