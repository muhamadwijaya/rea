// Market pages: one config per page. Instrument membership comes from the engine (instrument.market);
// news membership comes from the gateway (item.markets).

export const MARKETS = {
  forex: {
    key: 'forex',
    title: 'Forex & Komoditas',
    nav: 'FOREX',
    desc: 'Pair mayor & cross, USD/IDR, DXY, emas, perak dan minyak',
    defaultSymbol: 'EURUSD',
    defaultTf: '1h',
    tvScreener: 'forex',
    timeline: { feedMode: 'market', market: 'forex' },
    widgets: [['fx', 'HEATMAP FOREX'], ['cross', 'CROSS RATES']],
    outlooks: ['usd', 'gold', 'oil'],
    strength: true,
    eventCurrencies: null,
    newsCats: [['all', 'SEMUA'], ['forex', 'FOREX'], ['commodity', 'KOMODITAS'], ['macro', 'MAKRO & BANK SENTRAL']],
  },
  'saham-as': {
    key: 'saham-as',
    title: 'Saham Amerika',
    nav: 'SAHAM AS',
    desc: 'Saham big-tech & blue chip, S&P 500, Nasdaq 100, Dow, VIX dan yield US10Y',
    defaultSymbol: 'NVDA',
    defaultTf: '1h',
    tvScreener: 'america',
    timeline: { feedMode: 'market', market: 'stock' },
    widgets: [['stocks', 'HEATMAP S&P 500'], ['nasdaq', 'HEATMAP NASDAQ 100']],
    outlooks: ['us_equities', 'yields', 'usd'],
    strength: false,
    eventCurrencies: ['USD'],
    newsCats: [['all', 'SEMUA'], ['us', 'SAHAM AS'], ['macro', 'MAKRO & FED']],
  },
  'saham-idx': {
    key: 'saham-idx',
    title: 'Saham Indonesia',
    nav: 'SAHAM IDX',
    desc: 'Saham BEI (bank, telko, tambang, konsumer) dan IHSG',
    defaultSymbol: 'BBCA',
    defaultTf: '1h',
    tvScreener: 'indonesia',
    timeline: { feedMode: 'symbol', symbol: 'IDX:COMPOSITE' },
    widgets: [['idx', 'KUOTASI SAHAM IDX']],
    outlooks: ['idx', 'usd'],
    strength: false,
    eventCurrencies: ['USD', 'CNY', 'IDR'],
    newsCats: null,
  },
  kripto: {
    key: 'kripto',
    title: 'Kripto',
    nav: 'KRIPTO',
    desc: 'Bitcoin, Ethereum dan altcoin utama terhadap USD',
    defaultSymbol: 'BTCUSD',
    defaultTf: '4h',
    tvScreener: 'crypto',
    timeline: { feedMode: 'market', market: 'crypto' },
    widgets: [['crypto', 'HEATMAP KRIPTO']],
    outlooks: ['crypto'],
    strength: false,
    eventCurrencies: ['USD'],
    newsCats: null,
  },
};

export const MARKET_KEYS = Object.keys(MARKETS);

export const rowsOf = (snapshot, market) =>
  (snapshot?.rows || []).filter((r) => r.instrument.market === market);
