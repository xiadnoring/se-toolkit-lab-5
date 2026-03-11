import { useState, useEffect } from 'react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js'
import { Bar, Line } from 'react-chartjs-2'

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
)

const STORAGE_KEY = 'api_key'
const LABS = ['lab-01', 'lab-02', 'lab-03', 'lab-04', 'lab-05']

// API Response types
interface ScoreBucket {
  bucket: string
  count: number
}

interface TimelineEntry {
  date: string
  submissions: number
}

interface PassRateEntry {
  task: string
  avg_score: number
  attempts: number
}

// Chart data types for react-chartjs-2
interface ChartData {
  labels: string[]
  datasets: {
    label: string
    data: number[]
    backgroundColor?: string | string[]
    borderColor?: string
    fill?: boolean
    tension?: number
  }[]
}

type FetchState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; message: string }

interface DashboardData {
  scores: FetchState<ScoreBucket[]>
  timeline: FetchState<TimelineEntry[]>
  passRates: FetchState<PassRateEntry[]>
}

function Dashboard() {
  const [selectedLab, setSelectedLab] = useState<string>('lab-04')
  const [data, setData] = useState<DashboardData>({
    scores: { status: 'idle' },
    timeline: { status: 'idle' },
    passRates: { status: 'idle' },
  })

  const token = localStorage.getItem(STORAGE_KEY) ?? ''

  useEffect(() => {
    if (!token) return

    const fetchScores = async () => {
      setData((prev) => ({ ...prev, scores: { status: 'loading' } }))
      try {
        const res = await fetch(`/analytics/scores?lab=${selectedLab}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const jsonData: ScoreBucket[] = await res.json()
        setData((prev) => ({ ...prev, scores: { status: 'success', data: jsonData } }))
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error'
        setData((prev) => ({ ...prev, scores: { status: 'error', message } }))
      }
    }

    const fetchTimeline = async () => {
      setData((prev) => ({ ...prev, timeline: { status: 'loading' } }))
      try {
        const res = await fetch(`/analytics/timeline?lab=${selectedLab}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const jsonData: TimelineEntry[] = await res.json()
        setData((prev) => ({ ...prev, timeline: { status: 'success', data: jsonData } }))
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error'
        setData((prev) => ({ ...prev, timeline: { status: 'error', message } }))
      }
    }

    const fetchPassRates = async () => {
      setData((prev) => ({ ...prev, passRates: { status: 'loading' } }))
      try {
        const res = await fetch(`/analytics/pass-rates?lab=${selectedLab}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const jsonData: PassRateEntry[] = await res.json()
        setData((prev) => ({ ...prev, passRates: { status: 'success', data: jsonData } }))
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error'
        setData((prev) => ({ ...prev, passRates: { status: 'error', message } }))
      }
    }

    fetchScores()
    fetchTimeline()
    fetchPassRates()
  }, [selectedLab, token])

  // Prepare bar chart data for score buckets
  const scoreChartData: ChartData = {
    labels:
      data.scores.status === 'success'
        ? data.scores.data.map((item) => item.bucket)
        : [],
    datasets: [
      {
        label: 'Number of Students',
        data:
          data.scores.status === 'success'
            ? data.scores.data.map((item) => item.count)
            : [],
        backgroundColor: ['#ef4444', '#f97316', '#eab308', '#22c55e'],
      },
    ],
  }

  // Prepare line chart data for timeline
  const timelineChartData: ChartData = {
    labels:
      data.timeline.status === 'success'
        ? data.timeline.data.map((item) => item.date)
        : [],
    datasets: [
      {
        label: 'Submissions',
        data:
          data.timeline.status === 'success'
            ? data.timeline.data.map((item) => item.submissions)
            : [],
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59, 130, 246, 0.1)',
        fill: true,
        tension: 0.3,
      },
    ],
  }

  const chartOptions = {
    responsive: true,
    plugins: {
      legend: {
        position: 'top' as const,
      },
    },
    scales: {
      y: {
        beginAtZero: true,
        ticks: {
          stepSize: 1,
        },
      },
    },
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Analytics Dashboard</h1>
        <div className="lab-selector">
          <label htmlFor="lab-select">Select Lab: </label>
          <select
            id="lab-select"
            value={selectedLab}
            onChange={(e) => setSelectedLab(e.target.value)}
          >
            {LABS.map((lab) => (
              <option key={lab} value={lab}>
                {lab}
              </option>
            ))}
          </select>
        </div>
      </header>

      <div className="dashboard-content">
        {/* Score Distribution Bar Chart */}
        <section className="chart-section">
          <h2>Score Distribution</h2>
          {data.scores.status === 'loading' && <p>Loading...</p>}
          {data.scores.status === 'error' && <p>Error: {data.scores.message}</p>}
          {data.scores.status === 'success' && (
            <Bar data={scoreChartData} options={chartOptions} />
          )}
        </section>

        {/* Timeline Line Chart */}
        <section className="chart-section">
          <h2>Submission Timeline</h2>
          {data.timeline.status === 'loading' && <p>Loading...</p>}
          {data.timeline.status === 'error' && <p>Error: {data.timeline.message}</p>}
          {data.timeline.status === 'success' && (
            <Line data={timelineChartData} options={chartOptions} />
          )}
        </section>

        {/* Pass Rates Table */}
        <section className="table-section">
          <h2>Pass Rates by Task</h2>
          {data.passRates.status === 'loading' && <p>Loading...</p>}
          {data.passRates.status === 'error' && <p>Error: {data.passRates.message}</p>}
          {data.passRates.status === 'success' && (
            <table>
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Avg Score</th>
                  <th>Attempts</th>
                </tr>
              </thead>
              <tbody>
                {data.passRates.data.map((entry) => (
                  <tr key={entry.task}>
                    <td>{entry.task}</td>
                    <td>{entry.avg_score.toFixed(1)}</td>
                    <td>{entry.attempts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  )
}

export default Dashboard
