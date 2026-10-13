{{- define "podcast-agent.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "podcast-agent.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "podcast-agent.labels" -}}
app.kubernetes.io/name: {{ include "podcast-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "podcast-agent.selector" -}}
app.kubernetes.io/name: {{ include "podcast-agent.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "podcast-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "podcast-agent.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Shared container settings: env, security, writable scratch volumes for a read-only root fs */}}
{{- define "podcast-agent.containerCommon" -}}
image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
imagePullPolicy: {{ .Values.image.pullPolicy }}
securityContext:
  {{- toYaml .Values.securityContext | nindent 2 }}
envFrom:
  - configMapRef:
      name: {{ include "podcast-agent.fullname" . }}
  - secretRef:
      name: {{ .Values.secrets.name }}
volumeMounts:
  - name: tmp
    mountPath: /tmp
  - name: cache
    mountPath: /cache
{{- end -}}

{{- define "podcast-agent.volumes" -}}
volumes:
  - name: tmp
    emptyDir: {}
  - name: cache
    emptyDir:
      sizeLimit: 1Gi
{{- end -}}
