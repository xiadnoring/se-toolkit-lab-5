import { useEffect, useState } from 'react'
import {
    Chart as ChartJS,
    CategoryScale,
    LinearScale,
    BarElement,
    PointElement,
    LineElement,
    Title,
    Tooltip,
    Legend,
    ChartOptions,
} from 'chart.js'
import { Bar, Line } from 'react-chartjs-2'

ChartJS.register(
    CategoryScale,
    LinearScale,
    BarElement,
    PointElement,
    LineElement,
    Title,
    Tooltip,
    Legend
)

const STORAGE_KEY = 'api_key'

interface ScoreBucket {
    bucket: string
    count: number
}

interface TimelinePoint {
    date: string // ISO date
    submissions: number
}

interface PassRate {
    task: string
    avg_score: number
    attempts: number
}

interface LabOption {
    id: string
    label: string
}

const LABS: LabOption[] = [
    { id: 'lab-01', label: 'Lab 01' },
    { id: 'lab-02', label: 'Lab 02' },
    { id: 'lab-03', label: 'Lab 03' },
    { id: 'lab-04', label: 'Lab 04' },
    { id: 'lab-05', label: 'Lab 05' },
]

export default function Dashboard() {
    const [lab, setLab] = useState<string>(LABS[0].id)
    const [scoreBuckets, setScoreBuckets] = useState<ScoreBucket[]>([])
    const [timeline, setTimeline] = useState<TimelinePoint[]>([])
    const [passRates, setPassRates] = useState<PassRate[]>([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    const token = localStorage.getItem(STORAGE_KEY) ?? ''

    useEffect(() => {
        let cancelled = false
        async function fetchAll() {
            setLoading(true)
            setError(null)
            try {
                const headers = { Authorization: `Bearer ${token}` }
                const [scoresRes, timelineRes, passRatesRes] = await Promise.all([
                    fetch(`/analytics/scores?lab=${encodeURIComponent(lab)}`, { headers }),
                    fetch(`/analytics/timeline?lab=${encodeURIComponent(lab)}`, { headers }),
                    fetch(`/analytics/pass-rates?lab=${encodeURIComponent(lab)}`, { headers }),
                ])
                if (!scoresRes.ok) throw new Error('Failed to fetch scores')
                if (!timelineRes.ok) throw new Error('Failed to fetch timeline')
                if (!passRatesRes.ok) throw new Error('Failed to fetch pass rates')
                const [scores, timelineData, passRatesData]: [
                    ScoreBucket[],
                    TimelinePoint[],
                    PassRate[]
                ] = await Promise.all([
                    scoresRes.json(),
                    timelineRes.json(),
                    passRatesRes.json(),
                ])
                if (!cancelled) {
                    setScoreBuckets(scores)
                    setTimeline(timelineData)
                    setPassRates(passRatesData)
                }
            } catch (err) {
                setError(err instanceof Error ? err.message : String(err))
            } finally {
                if (!cancelled) setLoading(false)
            }
        }
        fetchAll()
        return () => {
            cancelled = true
        }
    }, [lab, token])

    // Bar chart data for score buckets
    const barData = {
        labels: scoreBuckets.map((b) => b.bucket),
        datasets: [
            {
                label: 'Count',
                data: scoreBuckets.map((b) => b.count),
                backgroundColor: 'rgba(54, 162, 235, 0.6)',
            },
        ],
    }
    const barOptions: ChartOptions<'bar'> = {
        responsive: true,
        plugins: {
            legend: { display: false },
            title: { display: true, text: 'Score Distribution' },
        },
    }

    // Line chart data for timeline
    const lineData = {
        labels: timeline.map((t) => t.date),
        datasets: [
            {
                label: 'Submissions',
                data: timeline.map((t) => t.submissions),
                borderColor: 'rgba(255, 99, 132, 0.8)',
                backgroundColor: 'rgba(255, 99, 132, 0.2)',
                tension: 0.2,
            },
        ],
    }
    const lineOptions: ChartOptions<'line'> = {
        responsive: true,
        plugins: {
            legend: { display: false },
            title: { display: true, text: 'Submissions Over Time' },
        },
        scales: {
            x: { title: { display: true, text: 'Date' } },
            y: { title: { display: true, text: 'Submissions' }, beginAtZero: true },
        },
    }

    return (
        <div style={{ maxWidth: 900, margin: '2rem auto', padding: '1rem' }}>
            <h1>Lab Dashboard</h1>
            <label>
                Select Lab:{' '}
                <select value={lab} onChange={(e) => setLab(e.target.value)}>
                    {LABS.map((l) => (
                        <option key={l.id} value={l.id}>
                            {l.label}
                        </option>
                    ))}
                </select>
            </label>
            {loading && <p>Loading...</p>}
            {error && <p style={{ color: 'red' }}>Error: {error}</p>}

            {!loading && !error && (
                <>
                    <div style={{ margin: '2rem 0' }}>
                        <Bar data={barData} options={barOptions} />
                    </div>
                    <div style={{ margin: '2rem 0' }}>
                        <Line data={lineData} options={lineOptions} />
                    </div>
                    <div style={{ margin: '2rem 0' }}>
                        <h2>Pass Rates per Task</h2>
                        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                            <thead>
                                <tr>
                                    <th style={{ borderBottom: '1px solid #ccc' }}>Task</th>
                                    <th style={{ borderBottom: '1px solid #ccc' }}>Avg. Score</th>
                                    <th style={{ borderBottom: '1px solid #ccc' }}>Attempts</th>
                                </tr>
                            </thead>
                            <tbody>
                                {passRates.map((pr) => (
                                    <tr key={pr.task}>
                                        <td style={{ borderBottom: '1px solid #eee' }}>{pr.task}</td>
                                        <td style={{ borderBottom: '1px solid #eee' }}>{pr.avg_score}</td>
                                        <td style={{ borderBottom: '1px solid #eee' }}>{pr.attempts}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </div>
    )
}