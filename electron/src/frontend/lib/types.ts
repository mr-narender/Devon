export type File<T = any> = {
    id: string
    name: string
    path: string
    language: string
    value: T
    icon?: string
    agentHasOpen?: boolean
}

export type Model = {
    id: string
    name: string
    company: string
    comingSoon?: boolean
    apiKeyUrl?: string
}

export type Message = {
    text: string
    type:
        | 'user'
        | 'agent'
        | 'command'
        | 'tool'
        | 'task'
        | 'thought'
        | 'error'
        | 'shellCommand'
        | 'shellResponse'
        | 'rateLimit'
}

// Make sure this is up-to-date with server.py @app.get("/sessions/{session}/config")
export type AgentConfig = {
    model: string
    versioning_type: string
    checkpoints: Checkpoint[]
    versioning_metadata: VersioningMetadata
}

type Checkpoint = {
    commit_hash: string
    commit_message: string
    agent_history: any[]
    event_id: number
}

type VersioningMetadata = {
    current_branch: string
    old_branch: string
    initial_commit: string
}
