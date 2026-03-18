import React, { useState, useEffect, useCallback } from 'react'
import {
    getUsers, createUser, updateUser, approveUser,
    resetUserPassword, deleteUser, getAuditLog
} from '../api'
import { useAuth } from '../contexts/AuthContext'

const ROLES = ['user', 'editor', 'admin']

function StatusBadge({ user }) {
    if (!user.is_active) {
        return <span className="badge badge-pending">Pending</span>
    }
    return <span className="badge badge-active">Active</span>
}

function RoleBadge({ role }) {
    const colors = {
        superadmin: 'badge-superadmin',
        admin: 'badge-admin',
        editor: 'badge-editor',
        user: 'badge-user',
    }
    return <span className={`badge ${colors[role] || ''}`}>{role}</span>
}

function CreateUserModal({ onClose, onCreated }) {
    const [form, setForm] = useState({ username: '', email: '', password: '', role: 'user' })
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        setLoading(true)
        try {
            const user = await createUser(form)
            onCreated(user)
            onClose()
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal" onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                    <h2 style={{ margin: 0 }}>Create User</h2>
                    <button className="btn btn-icon btn-secondary" onClick={onClose}>✕</button>
                </div>
                {error && <div className="alert alert-error">⚠️ {error}</div>}
                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label>Username</label>
                        <input className="form-input" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} required minLength={3} maxLength={50} />
                    </div>
                    <div className="form-group">
                        <label>Email</label>
                        <input type="email" className="form-input" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} required />
                    </div>
                    <div className="form-group">
                        <label>Temporary Password</label>
                        <input type="password" className="form-input" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} required minLength={6} autoComplete="new-password" />
                    </div>
                    <div className="form-group">
                        <label>Role</label>
                        <select className="form-input" value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}>
                            {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                        </select>
                    </div>
                    <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '12px' }}>
                        User will be required to change their password on first login.
                    </p>
                    <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                        <button type="submit" className="btn btn-primary" disabled={loading}>
                            {loading ? <div className="spinner"></div> : 'Create User'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    )
}

function ResetPasswordModal({ user, onClose, onReset }) {
    const [newPassword, setNewPassword] = useState('')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        setLoading(true)
        try {
            await resetUserPassword(user.id, newPassword)
            onReset()
            onClose()
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal" onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                    <h2 style={{ margin: 0 }}>Reset Password — {user.username}</h2>
                    <button className="btn btn-icon btn-secondary" onClick={onClose}>✕</button>
                </div>
                {error && <div className="alert alert-error">⚠️ {error}</div>}
                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label>New Password</label>
                        <input
                            type="password"
                            className="form-input"
                            value={newPassword}
                            onChange={e => setNewPassword(e.target.value)}
                            required
                            minLength={6}
                            autoComplete="new-password"
                        />
                    </div>
                    <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '12px' }}>
                        User will be required to change this password on next login.
                    </p>
                    <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                        <button type="submit" className="btn btn-primary" disabled={loading}>
                            {loading ? <div className="spinner"></div> : 'Reset Password'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    )
}

function AuditLogTab() {
    const [entries, setEntries] = useState([])
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [actionFilter, setActionFilter] = useState('')
    const [loading, setLoading] = useState(false)
    const limit = 50

    const load = useCallback(async () => {
        setLoading(true)
        try {
            const data = await getAuditLog(page, limit, actionFilter || undefined)
            setEntries(data.entries)
            setTotal(data.total)
        } catch (err) {
            console.error('Failed to load audit log:', err)
        } finally {
            setLoading(false)
        }
    }, [page, actionFilter])

    useEffect(() => { load() }, [load])

    const ACTION_LABELS = {
        login: 'Login',
        login_failed: 'Login Failed',
        register_request: 'Access Request',
        password_changed: 'Password Changed',
        password_reset: 'Password Reset',
        role_change: 'Role Changed',
        user_created: 'User Created',
        user_deleted: 'User Deleted',
        user_approved: 'User Approved',
        user_updated: 'User Updated',
    }

    const ACTION_COLORS = {
        login: 'badge-active',
        login_failed: 'badge-error',
        register_request: 'badge-pending',
        password_changed: 'badge-editor',
        password_reset: 'badge-editor',
        user_created: 'badge-active',
        user_deleted: 'badge-error',
        user_approved: 'badge-active',
        user_updated: 'badge-admin',
    }

    const totalPages = Math.ceil(total / limit)

    return (
        <div>
            <div style={{ display: 'flex', gap: '12px', marginBottom: '16px', alignItems: 'center' }}>
                <select
                    className="form-input"
                    style={{ width: '200px' }}
                    value={actionFilter}
                    onChange={e => { setActionFilter(e.target.value); setPage(1) }}
                >
                    <option value="">All Actions</option>
                    {Object.entries(ACTION_LABELS).map(([k, v]) => (
                        <option key={k} value={k}>{v}</option>
                    ))}
                </select>
                <button className="btn btn-secondary" onClick={load}>Refresh</button>
                <span style={{ marginLeft: 'auto', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                    {total} entries
                </span>
            </div>

            {loading ? (
                <div style={{ textAlign: 'center', padding: '40px' }}><div className="spinner"></div></div>
            ) : (
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>User</th>
                            <th>Action</th>
                            <th>Target</th>
                            <th>Details</th>
                            <th>IP</th>
                        </tr>
                    </thead>
                    <tbody>
                        {entries.length === 0 ? (
                            <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '24px' }}>No entries found</td></tr>
                        ) : entries.map(e => (
                            <tr key={e.id}>
                                <td style={{ whiteSpace: 'nowrap', fontSize: '0.8rem' }}>
                                    {new Date(e.created_at).toLocaleString()}
                                </td>
                                <td>{e.username || <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                                <td>
                                    <span className={`badge ${ACTION_COLORS[e.action] || 'badge-user'}`}>
                                        {ACTION_LABELS[e.action] || e.action}
                                    </span>
                                </td>
                                <td>{e.target_username || <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                                <td style={{ fontSize: '0.8rem', maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {e.details || <span style={{ color: 'var(--text-muted)' }}>—</span>}
                                </td>
                                <td style={{ fontSize: '0.8rem' }}>{e.ip_address || '—'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}

            {totalPages > 1 && (
                <div style={{ display: 'flex', gap: '8px', justifyContent: 'center', marginTop: '16px' }}>
                    <button className="btn btn-secondary" disabled={page === 1} onClick={() => setPage(p => p - 1)}>Previous</button>
                    <span style={{ lineHeight: '32px', fontSize: '0.85rem' }}>Page {page} of {totalPages}</span>
                    <button className="btn btn-secondary" disabled={page === totalPages} onClick={() => setPage(p => p + 1)}>Next</button>
                </div>
            )}
        </div>
    )
}

function UserManagementPage() {
    const { user: currentUser } = useAuth()
    const [users, setUsers] = useState([])
    const [filter, setFilter] = useState('all')
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const [activeTab, setActiveTab] = useState('users')
    const [showCreateModal, setShowCreateModal] = useState(false)
    const [resetTarget, setResetTarget] = useState(null)

    const loadUsers = useCallback(async () => {
        setLoading(true)
        setError('')
        try {
            const data = await getUsers(filter === 'all' ? undefined : filter)
            setUsers(data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }, [filter])

    useEffect(() => { loadUsers() }, [loadUsers])

    const handleApprove = async (user) => {
        try {
            await approveUser(user.id)
            loadUsers()
        } catch (err) {
            setError(err.message)
        }
    }

    const handleToggleActive = async (user) => {
        try {
            await updateUser(user.id, { is_active: !user.is_active })
            loadUsers()
        } catch (err) {
            setError(err.message)
        }
    }

    const handleRoleChange = async (user, newRole) => {
        try {
            await updateUser(user.id, { role: newRole })
            loadUsers()
        } catch (err) {
            setError(err.message)
        }
    }

    const handleDelete = async (user) => {
        if (!confirm(`Delete user "${user.username}"? This cannot be undone.`)) return
        try {
            await deleteUser(user.id)
            loadUsers()
        } catch (err) {
            setError(err.message)
        }
    }

    const pendingCount = users.filter(u => !u.is_active).length

    return (
        <div className="page-container">
            <div className="page-header">
                <div>
                    <h1 className="page-title">User Management</h1>
                    <p className="page-subtitle">Manage user accounts, roles, and access requests</p>
                </div>
                {activeTab === 'users' && (
                    <button className="btn btn-primary" onClick={() => setShowCreateModal(true)}>
                        + Create User
                    </button>
                )}
            </div>

            {/* Tabs */}
            <div className="tab-bar" style={{ marginBottom: '20px' }}>
                <button
                    className={`tab-btn ${activeTab === 'users' ? 'active' : ''}`}
                    onClick={() => setActiveTab('users')}
                >
                    Users {pendingCount > 0 && <span className="badge badge-pending" style={{ marginLeft: '6px' }}>{pendingCount} pending</span>}
                </button>
                <button
                    className={`tab-btn ${activeTab === 'audit' ? 'active' : ''}`}
                    onClick={() => setActiveTab('audit')}
                >
                    Audit Log
                </button>
            </div>

            {error && <div className="alert alert-error">⚠️ {error}</div>}

            {activeTab === 'users' && (
                <>
                    {/* Filter tabs */}
                    <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
                        {['all', 'pending', 'active'].map(f => (
                            <button
                                key={f}
                                className={`btn ${filter === f ? 'btn-primary' : 'btn-secondary'}`}
                                style={{ padding: '4px 12px', fontSize: '0.85rem' }}
                                onClick={() => setFilter(f)}
                            >
                                {f.charAt(0).toUpperCase() + f.slice(1)}
                            </button>
                        ))}
                    </div>

                    {loading ? (
                        <div style={{ textAlign: 'center', padding: '40px' }}><div className="spinner"></div></div>
                    ) : (
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Username</th>
                                    <th>Email</th>
                                    <th>Role</th>
                                    <th>Status</th>
                                    <th>Created</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {users.length === 0 ? (
                                    <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '24px' }}>No users found</td></tr>
                                ) : users.map(u => {
                                    const isSuperadmin = u.role === 'superadmin'
                                    const isSelf = u.id === currentUser?.id
                                    return (
                                        <tr key={u.id}>
                                            <td>
                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                    {isSuperadmin && <span title="Superadmin — protected">🔒</span>}
                                                    <strong>{u.username}</strong>
                                                    {isSelf && <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>(you)</span>}
                                                </div>
                                            </td>
                                            <td>{u.email}</td>
                                            <td>
                                                {isSuperadmin || isSelf ? (
                                                    <RoleBadge role={u.role} />
                                                ) : (
                                                    <select
                                                        className="form-input"
                                                        style={{ padding: '2px 6px', fontSize: '0.8rem', width: 'auto' }}
                                                        value={u.role}
                                                        onChange={e => handleRoleChange(u, e.target.value)}
                                                    >
                                                        {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                                                    </select>
                                                )}
                                            </td>
                                            <td><StatusBadge user={u} /></td>
                                            <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                                                {new Date(u.created_at).toLocaleDateString()}
                                            </td>
                                            <td>
                                                <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                                                    {!u.is_active && (
                                                        <button
                                                            className="btn btn-secondary"
                                                            style={{ padding: '2px 8px', fontSize: '0.8rem' }}
                                                            onClick={() => handleApprove(u)}
                                                        >
                                                            Approve
                                                        </button>
                                                    )}
                                                    {!isSuperadmin && !isSelf && (
                                                        <>
                                                            <button
                                                                className="btn btn-secondary"
                                                                style={{ padding: '2px 8px', fontSize: '0.8rem' }}
                                                                onClick={() => setResetTarget(u)}
                                                            >
                                                                Reset PW
                                                            </button>
                                                            {u.is_active && (
                                                                <button
                                                                    className="btn btn-secondary"
                                                                    style={{ padding: '2px 8px', fontSize: '0.8rem' }}
                                                                    onClick={() => handleToggleActive(u)}
                                                                >
                                                                    Deactivate
                                                                </button>
                                                            )}
                                                            <button
                                                                className="btn btn-danger"
                                                                style={{ padding: '2px 8px', fontSize: '0.8rem' }}
                                                                onClick={() => handleDelete(u)}
                                                            >
                                                                Delete
                                                            </button>
                                                        </>
                                                    )}
                                                </div>
                                            </td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    )}
                </>
            )}

            {activeTab === 'audit' && <AuditLogTab />}

            {showCreateModal && (
                <CreateUserModal
                    onClose={() => setShowCreateModal(false)}
                    onCreated={() => loadUsers()}
                />
            )}
            {resetTarget && (
                <ResetPasswordModal
                    user={resetTarget}
                    onClose={() => setResetTarget(null)}
                    onReset={() => loadUsers()}
                />
            )}
        </div>
    )
}

export default UserManagementPage
