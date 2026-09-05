import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
    getUsers, createUser, updateUser, approveUser,
    resetUserPassword, deleteUser, getAuditLog,
    createInvite, getInvites, revokeInvite
} from '../api'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '../lib/passwordPolicy'
import { useAuth } from '../contexts/AuthContext'
// Audit-log and user timestamps are naive UTC (issue #216).
import { formatDate, formatDateTime } from '../lib/datetime'
import Modal from '../components/Modal'
import './UserManagementPage.css'

const ROLES = ['user', 'editor', 'admin']

/* ── FilterPill ────────────────────────────────────────────────────── */
function FilterPill({ label, value, options, onChange }) {
    const [open, setOpen] = useState(false)
    const ref = useRef(null)

    useEffect(() => {
        if (!open) return
        const handleClick = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
        document.addEventListener('mousedown', handleClick)
        return () => document.removeEventListener('mousedown', handleClick)
    }, [open])

    const selected = options.find(o => o.value === value)
    const isFiltered = value !== ''

    return (
        <div className="admin-filter-pill" ref={ref}>
            <button
                className={`admin-filter-pill-btn${isFiltered ? ' active' : ''}${open ? ' open' : ''}`}
                onClick={() => setOpen(o => !o)}
            >
                {label}{selected && selected.value !== '' ? `: ${selected.label}` : ''}
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="11" height="11">
                    <polyline points="6 9 12 15 18 9" />
                </svg>
            </button>
            {open && (
                <div className="admin-filter-pill-dropdown">
                    {options.map(o => (
                        <button
                            key={o.value}
                            className={`admin-filter-pill-option${o.value === value ? ' selected' : ''}`}
                            onClick={() => { onChange(o.value); setOpen(false) }}
                        >
                            {o.label}
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}

/* ── Badges ────────────────────────────────────────────────────────── */
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

/* ── CreateUserModal ───────────────────────────────────────────────── */
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
        <Modal onClose={onClose} labelledBy="create-user-title">
                <div className="admin-modal-header">
                    <h2 id="create-user-title">Create User</h2>
                    <button className="btn btn-icon btn-secondary" onClick={onClose} aria-label="Close">✕</button>
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
                        <input type="password" className="form-input" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} required minLength={PASSWORD_MIN_LENGTH} maxLength={PASSWORD_MAX_LENGTH} autoComplete="new-password" />
                    </div>
                    <div className="form-group">
                        <label>Role</label>
                        <select className="form-input" value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}>
                            {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                        </select>
                    </div>
                    <p className="admin-modal-note">
                        User will be required to change their password on first login.
                    </p>
                    <div className="admin-modal-footer">
                        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                        <button type="submit" className="btn btn-primary" disabled={loading}>
                            {loading ? <div className="spinner"></div> : 'Create User'}
                        </button>
                    </div>
                </form>
        </Modal>
    )
}

/* ── ResetPasswordModal ────────────────────────────────────────────── */
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
        <Modal onClose={onClose} labelledBy="reset-password-title">
                <div className="admin-modal-header">
                    <h2 id="reset-password-title">Reset Password — {user.username}</h2>
                    <button className="btn btn-icon btn-secondary" onClick={onClose} aria-label="Close">✕</button>
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
                            minLength={PASSWORD_MIN_LENGTH} maxLength={PASSWORD_MAX_LENGTH}
                            autoComplete="new-password"
                        />
                    </div>
                    <p className="admin-modal-note">
                        User will be required to change this password on next login.
                    </p>
                    <div className="admin-modal-footer">
                        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                        <button type="submit" className="btn btn-primary" disabled={loading}>
                            {loading ? <div className="spinner"></div> : 'Reset Password'}
                        </button>
                    </div>
                </form>
        </Modal>
    )
}

/* ── AuditLogTab ───────────────────────────────────────────────────── */
const ACTION_LABELS = {
    login: 'Login',
    login_failed: 'Login Failed',
    // Per-username throttle refused the attempt with 429 (issue #296).
    login_locked: 'Login Locked',
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
    login_locked: 'badge-error',
    register_request: 'badge-pending',
    password_changed: 'badge-editor',
    password_reset: 'badge-editor',
    user_created: 'badge-active',
    user_deleted: 'badge-error',
    user_approved: 'badge-active',
    user_updated: 'badge-admin',
}

const ACTION_FILTER_OPTIONS = [
    { value: '', label: 'All Actions' },
    ...Object.entries(ACTION_LABELS).map(([k, v]) => ({ value: k, label: v }))
]

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

    const totalPages = Math.ceil(total / limit)

    return (
        <div>
            <div className="admin-audit-toolbar">
                <FilterPill
                    label="Action"
                    value={actionFilter}
                    options={ACTION_FILTER_OPTIONS}
                    onChange={(val) => { setActionFilter(val); setPage(1) }}
                />
                <button className="btn btn-secondary" onClick={load}>Refresh</button>
                <span className="admin-audit-count">{total} entries</span>
            </div>

            {loading ? (
                <div style={{ textAlign: 'center', padding: '40px' }}><div className="spinner"></div></div>
            ) : (
                <div className="admin-table-card">
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
                                        {formatDateTime(e.created_at)}
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
                </div>
            )}

            {totalPages > 1 && (
                <div className="admin-pagination">
                    <button className="btn btn-secondary" disabled={page === 1} onClick={() => setPage(p => p - 1)}>Previous</button>
                    <span>Page {page} of {totalPages}</span>
                    <button className="btn btn-secondary" disabled={page === totalPages} onClick={() => setPage(p => p + 1)}>Next</button>
                </div>
            )}
        </div>
    )
}

/* ── UserManagementSection (named export for embedding) ───────────── */
/* ── InvitesTab (issue #210) ─────────────────────────────────── */

/**
 * Invites are the whole of `registration_mode = "invite"`, and this is the only
 * place one can be issued — there is no CLI.
 *
 * The code comes back exactly once, from the create call, and is held in local
 * state until the admin navigates away. Nothing re-reads it: the server stores
 * only a sha256, so a "show me that code again" button could not be built even
 * if it were wanted.
 */
function InvitesTab() {
    const [invites, setInvites] = useState([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const [issued, setIssued] = useState(null)

    const load = useCallback(async () => {
        setLoading(true)
        try {
            setInvites(await getInvites())
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { load() }, [load])

    const handleCreate = async () => {
        setError('')
        try {
            const invite = await createInvite()
            setIssued(invite)
            load()
        } catch (err) {
            setError(err.message)
        }
    }

    const handleRevoke = async (invite) => {
        if (!confirm(`Revoke invite #${invite.id}? Anyone holding the code will no longer be able to use it.`)) return
        setError('')
        try {
            await revokeInvite(invite.id)
            load()
        } catch (err) {
            setError(err.message)
        }
    }

    return (
        <div>
            <div className="admin-toolbar">
                <div className="admin-toolbar-left">
                    <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                        Single-use codes. They matter only while registration mode is
                        <strong> Invite</strong>; the account they create still needs approving.
                    </p>
                </div>
                <div className="admin-toolbar-right">
                    <button className="btn btn-primary" onClick={handleCreate}>+ Create Invite</button>
                </div>
            </div>

            {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>⚠️ {error}</div>}

            {issued && (
                <div className="alert alert-success" style={{ marginBottom: 16 }}>
                    <div>Invite code: <code>{issued.code}</code></div>
                    <div style={{ fontSize: '0.8rem', marginTop: 4 }}>
                        Copy it now — it will not be shown again. The server keeps only a hash.
                    </div>
                </div>
            )}

            {loading ? (
                <div style={{ textAlign: 'center', padding: '40px' }}><div className="spinner"></div></div>
            ) : (
                <div className="admin-table-card">
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Status</th>
                                <th>Created</th>
                                <th>Expires</th>
                                <th>Created by</th>
                                <th>Used by</th>
                                <th></th>
                            </tr>
                        </thead>
                        <tbody>
                            {invites.length === 0 && (
                                <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                                    No invites yet.
                                </td></tr>
                            )}
                            {invites.map(inv => (
                                <tr key={inv.id}>
                                    <td>{inv.id}</td>
                                    <td>{inv.status}</td>
                                    <td>{formatDate(inv.created_at)}</td>
                                    <td>{formatDate(inv.expires_at)}</td>
                                    <td>{inv.created_by || '—'}</td>
                                    <td>{inv.used_by || '—'}</td>
                                    <td>
                                        {/* A spent invite has nothing left to revoke, and
                                            deleting the row would erase which account it
                                            produced. */}
                                        {inv.status !== 'used' && (
                                            <button
                                                className="btn btn-danger"
                                                style={{ padding: '2px 8px', fontSize: '0.8rem' }}
                                                onClick={() => handleRevoke(inv)}
                                            >
                                                Revoke
                                            </button>
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    )
}

export function UserManagementSection() {
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
        <div>
            {/* ── Toolbar ── */}
            <div className="admin-toolbar">
                <div className="admin-toolbar-left">
                    {/* Main tab switcher */}
                    <div className="library-filter-pills">
                        <button
                            className={`library-filter-pill${activeTab === 'users' ? ' active' : ''}`}
                            onClick={() => setActiveTab('users')}
                        >
                            Users
                            {pendingCount > 0 && (
                                <span className="badge badge-pending" style={{ marginLeft: 6 }}>{pendingCount}</span>
                            )}
                        </button>
                        <button
                            className={`library-filter-pill${activeTab === 'invites' ? ' active' : ''}`}
                            onClick={() => setActiveTab('invites')}
                        >
                            Invites
                        </button>
                        <button
                            className={`library-filter-pill${activeTab === 'audit' ? ' active' : ''}`}
                            onClick={() => setActiveTab('audit')}
                        >
                            Audit Log
                        </button>
                    </div>

                    {/* Sub-filter pills — Users tab only */}
                    {activeTab === 'users' && (
                        <div className="library-filter-pills">
                            {['all', 'pending', 'active'].map(f => (
                                <button
                                    key={f}
                                    className={`library-filter-pill${filter === f ? ' active' : ''}`}
                                    onClick={() => setFilter(f)}
                                >
                                    {f.charAt(0).toUpperCase() + f.slice(1)}
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                <div className="admin-toolbar-right">
                    {activeTab === 'users' && (
                        <button className="btn btn-primary" onClick={() => setShowCreateModal(true)}>
                            + Create User
                        </button>
                    )}
                </div>
            </div>

            {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>⚠️ {error}</div>}

            {/* ── Users tab ── */}
            {activeTab === 'users' && (
                <>
                    {loading ? (
                        <div style={{ textAlign: 'center', padding: '40px' }}><div className="spinner"></div></div>
                    ) : (
                        <div className="admin-table-card">
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
                                                    {formatDate(u.created_at)}
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
                        </div>
                    )}
                </>
            )}

            {/* ── Invites tab ── */}
            {activeTab === 'invites' && <InvitesTab />}

            {/* ── Audit Log tab ── */}
            {activeTab === 'audit' && <AuditLogTab />}

            {/* ── Modals ── */}
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
