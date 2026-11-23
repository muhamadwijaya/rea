using System.Globalization;
using Sharx.Core.Configuration;
using Sharx.Core.Mapping;

namespace Sharx.Core.Counter;

/// <summary>Hasil perhitungan counter untuk satu job/record (belum disimpan).</summary>
public sealed record CounterDecision
{
    public required string Template { get; init; }
    public required string Operator { get; init; }
    public required string BusinessDate { get; init; }

    /// <summary>Nilai counter terakhir yang dipakai template ini (atau nilai awal untuk template baru).</summary>
    public required long BaseValue { get; init; }

    /// <summary>Nilai counter yang akan dicetak berikutnya.</summary>
    public required long Value { get; init; }

    public required string Text { get; init; }
    public required bool TemplateChanged { get; init; }
    public required bool OperatorChanged { get; init; }
    public required bool DayRolled { get; init; }
    public required bool NewCounter { get; init; }
    public string? PreviousTemplate { get; init; }
    public string? PreviousOperator { get; init; }
    public long? ServerValue { get; init; }
    public required string Explanation { get; init; }
    public required long Epoch { get; init; }
    internal long BaseVersion { get; init; }
}

/// <summary>
/// Logika counter:
/// <list type="bullet">
/// <item>Counter disimpan per template untuk hari produksi yang sedang berjalan.</item>
/// <item>Template sama, operator berubah: counter lanjut dari hitungan terakhir.</item>
/// <item>Template berubah: counter pindah ke counter template baru (yang tersimpan, atau mulai baru).</item>
/// <item>Ganti hari atau tombol Reset Counter: semua counter dihapus.</item>
/// </list>
/// </summary>
public sealed class CounterEngine
{
    private readonly object _gate = new();
    private readonly string? _path;
    private readonly TimeProvider _time;
    private CounterState _state;

    public CounterEngine(string? path, TimeProvider time)
    {
        _path = path;
        _time = time;
        if (path is null)
        {
            _state = new CounterState();
            return;
        }

        var (state, status, error) = JsonFile.Load(path, () => new CounterState());
        state.Templates = new Dictionary<string, TemplateCounter>(state.Templates ?? [], StringComparer.Ordinal);
        state.BusinessDate ??= "";
        _state = state;
        LoadWarning = status == JsonFile.LoadStatus.Corrupt ? $"File counter rusak, mulai dari kosong: {error}" : null;
    }

    /// <summary>Peringatan saat memuat file counter (null jika normal).</summary>
    public string? LoadWarning { get; }

    /// <summary>Error terakhir saat menyimpan file counter (null jika normal).</summary>
    public string? LastPersistError { get; private set; }

    public event Action? Changed;

    public CounterState Snapshot()
    {
        lock (_gate)
        {
            return _state.Clone();
        }
    }

    /// <summary>Nomor reset; naik setiap Reset Counter / ganti hari / ubah manual.</summary>
    public long Epoch
    {
        get
        {
            lock (_gate)
            {
                return _state.Epoch;
            }
        }
    }

    public static string BusinessDateFor(DateTimeOffset localNow, string? dayStartTime)
    {
        var start = ParseDayStart(dayStartTime);
        var date = localNow.TimeOfDay < start ? localNow.Date.AddDays(-1) : localNow.Date;
        return date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
    }

    public static TimeSpan ParseDayStart(string? text) =>
        TimeSpan.TryParseExact(text?.Trim(), [@"hh\:mm", @"h\:mm"], CultureInfo.InvariantCulture, out var t)
        && t >= TimeSpan.Zero && t < TimeSpan.FromDays(1)
            ? t
            : TimeSpan.Zero;

    public static string Format(long value, int digits) =>
        value < 0
            ? value.ToString(CultureInfo.InvariantCulture)
            : value.ToString("D" + Math.Clamp(digits, 1, 18), CultureInfo.InvariantCulture);

    /// <summary>Nilai counter berikutnya setelah <paramref name="last"/>.</summary>
    public static long Next(long last, bool firstUse, CounterSettings settings)
    {
        var value = firstUse && !settings.IncrementBeforeUse ? last : last + settings.Step;
        if (settings.MaxValue > 0 && value > settings.MaxValue)
        {
            value = settings.IncrementBeforeUse ? settings.StartValue + settings.Step : settings.StartValue;
        }

        return value;
    }

    /// <summary>Hitung counter untuk record/job ini tanpa mengubah state.</summary>
    public CounterDecision Evaluate(ParsedRecord record, CounterSettings settings)
    {
        var date = BusinessDateFor(_time.GetLocalNow(), settings.DayStartTime);
        var template = record.Template;
        var op = record.Operator;
        var server = ParseServerCounter(record.ServerCounter);

        lock (_gate)
        {
            var dayRolled = _state.BusinessDate.Length > 0 && _state.BusinessDate != date;
            var templateChanged = !string.Equals(_state.ActiveTemplate, template, StringComparison.Ordinal);
            var operatorChanged = !string.Equals(_state.ActiveOperator, op, StringComparison.Ordinal);
            TemplateCounter? existing = null;
            if (!dayRolled)
            {
                _state.Templates.TryGetValue(template, out existing);
            }

            long baseValue;
            string why;
            if (existing is null)
            {
                var fromServer = settings.UseServerValueAsStart && server.HasValue;
                baseValue = fromServer ? server!.Value : settings.StartValue;
                var source = fromServer ? "counter server" : "nilai awal";
                why = dayRolled
                    ? $"Hari baru ({date}): counter direset, template {template} mulai dari {source} {baseValue}"
                    : $"Template {template} belum punya counter hari ini: mulai dari {source} {baseValue}";
            }
            else
            {
                baseValue = existing.Value;
                why = templateChanged
                    ? $"Template berubah ke {template}: lanjut counter tersimpan {Format(existing.Value, settings.Digits)}"
                    : operatorChanged
                        ? $"Operator berubah, template sama: counter lanjut dari {Format(existing.Value, settings.Digits)}"
                        : $"Counter lanjut dari {Format(existing.Value, settings.Digits)}";
            }

            var value = Next(baseValue, existing is null || existing.Records == 0, settings);
            return new CounterDecision
            {
                Template = template,
                Operator = op,
                BusinessDate = date,
                BaseValue = baseValue,
                Value = value,
                Text = Format(value, settings.Digits),
                TemplateChanged = templateChanged,
                OperatorChanged = operatorChanged,
                DayRolled = dayRolled,
                NewCounter = existing is null,
                PreviousTemplate = _state.ActiveTemplate,
                PreviousOperator = _state.ActiveOperator,
                ServerValue = server,
                Explanation = why,
                Epoch = dayRolled ? _state.Epoch + 1 : _state.Epoch,
                BaseVersion = _state.Version,
            };
        }
    }

    /// <summary>
    /// Mode counter per cetak: jadikan record ini job aktif (template/operator) tanpa menambah counter.
    /// Template baru mendapat counter dengan nilai awal. Mengembalikan false jika state berubah sejak Evaluate.
    /// </summary>
    public bool CommitJob(CounterDecision decision)
    {
        lock (_gate)
        {
            if (_state.Version != decision.BaseVersion)
            {
                return false;
            }

            ApplyDayLocked(decision);
            if (!_state.Templates.ContainsKey(decision.Template))
            {
                _state.Templates[decision.Template] = new TemplateCounter
                {
                    Value = decision.BaseValue,
                    Records = 0,
                    LastOperator = decision.Operator,
                    UpdatedAt = _time.GetLocalNow(),
                };
            }

            _state.ActiveTemplate = decision.Template;
            _state.ActiveOperator = decision.Operator;
            _state.Version++;
            PersistLocked();
        }

        Changed?.Invoke();
        return true;
    }

    /// <summary>
    /// Mode counter per data: simpan nilai counter setelah data berhasil dikirim.
    /// Mengembalikan false jika counter direset/diubah selama pengiriman (hasil tidak disimpan).
    /// </summary>
    public bool Commit(CounterDecision decision)
    {
        lock (_gate)
        {
            if (_state.Version != decision.BaseVersion)
            {
                return false;
            }

            ApplyDayLocked(decision);
            var previous = _state.Templates.GetValueOrDefault(decision.Template);
            _state.Templates[decision.Template] = new TemplateCounter
            {
                Value = decision.Value,
                Records = (previous?.Records ?? 0) + 1,
                LastOperator = decision.Operator,
                UpdatedAt = _time.GetLocalNow(),
            };
            _state.ActiveTemplate = decision.Template;
            _state.ActiveOperator = decision.Operator;
            _state.Version++;
            PersistLocked();
        }

        Changed?.Invoke();
        return true;
    }

    /// <summary>
    /// Mode counter per cetak: printer melapor <paramref name="value"/> sudah tercetak.
    /// Diabaikan (false) jika counter sudah direset sejak nilai itu dikirim (<paramref name="epoch"/> beda).
    /// </summary>
    public bool CommitPrint(string template, string op, long value, long epoch)
    {
        lock (_gate)
        {
            if (_state.Epoch != epoch)
            {
                return false;
            }

            var previous = _state.Templates.GetValueOrDefault(template);
            _state.Templates[template] = new TemplateCounter
            {
                Value = value,
                Records = (previous?.Records ?? 0) + 1,
                LastOperator = op,
                UpdatedAt = _time.GetLocalNow(),
            };
            _state.Version++;
            PersistLocked();
        }

        Changed?.Invoke();
        return true;
    }

    /// <summary>Nilai berikutnya untuk template yang sudah punya counter (null jika belum ada).</summary>
    public long? NextValue(string template, CounterSettings settings)
    {
        lock (_gate)
        {
            return _state.Templates.TryGetValue(template, out var c)
                ? Next(c.Value, c.Records == 0, settings)
                : null;
        }
    }

    /// <summary>Reset semua counter jika hari produksi sudah berganti. Mengembalikan tanggal lama jika reset.</summary>
    public string? RollDayIfNeeded(CounterSettings settings)
    {
        var date = BusinessDateFor(_time.GetLocalNow(), settings.DayStartTime);
        string previous;
        lock (_gate)
        {
            if (_state.BusinessDate == date)
            {
                return null;
            }

            previous = _state.BusinessDate;
            _state.BusinessDate = date;
            if (previous.Length > 0)
            {
                _state.Templates.Clear();
                _state.Epoch++;
            }

            _state.Version++;
            PersistLocked();
        }

        Changed?.Invoke();
        return previous.Length > 0 ? previous : null;
    }

    /// <summary>Reset counter (semua template jika <paramref name="template"/> null).</summary>
    /// <remarks>Selalu menaikkan versi/epoch sehingga data yang sedang dikirim tidak menyimpan counter lama.</remarks>
    public bool Reset(string? template)
    {
        bool removed;
        lock (_gate)
        {
            if (template is null)
            {
                removed = _state.Templates.Count > 0;
                _state.Templates.Clear();
            }
            else
            {
                removed = _state.Templates.Remove(template);
            }

            _state.Epoch++;
            _state.Version++;
            PersistLocked();
        }

        Changed?.Invoke();
        return removed;
    }

    /// <summary>Set manual nilai counter terakhir sebuah template.</summary>
    public void SetValue(string template, long value, CounterSettings settings)
    {
        lock (_gate)
        {
            if (_state.BusinessDate.Length == 0)
            {
                _state.BusinessDate = BusinessDateFor(_time.GetLocalNow(), settings.DayStartTime);
            }

            var previous = _state.Templates.GetValueOrDefault(template);
            _state.Templates[template] = new TemplateCounter
            {
                Value = value,
                Records = Math.Max(1, previous?.Records ?? 0),
                LastOperator = previous?.LastOperator,
                UpdatedAt = _time.GetLocalNow(),
            };
            _state.Epoch++;
            _state.Version++;
            PersistLocked();
        }

        Changed?.Invoke();
    }

    internal static long? ParseServerCounter(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return null;
        }

        return long.TryParse(text.Trim(), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var v)
            ? v
            : null;
    }

    private void ApplyDayLocked(CounterDecision decision)
    {
        if (_state.BusinessDate == decision.BusinessDate)
        {
            return;
        }

        if (decision.DayRolled)
        {
            _state.Templates.Clear();
            _state.Epoch++;
        }

        _state.BusinessDate = decision.BusinessDate;
    }

    private void PersistLocked()
    {
        if (_path is null)
        {
            return;
        }

        try
        {
            JsonFile.Save(_path, _state, keepBackup: false);
            LastPersistError = null;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            LastPersistError = ex.Message;
        }
    }
}
