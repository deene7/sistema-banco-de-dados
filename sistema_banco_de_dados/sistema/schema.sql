CREATE TABLE IF NOT EXISTS app_usuarios (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(150) NOT NULL,
    login VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    senha_hash VARCHAR(255) NOT NULL,
    perfil VARCHAR(20) NOT NULL,
    ativo TINYINT NOT NULL DEFAULT 1,
    gerenciar_usuarios TINYINT NOT NULL DEFAULT 0,
    auth_versao INT NOT NULL DEFAULT 1,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_app_perfil CHECK (perfil IN ('ADM','TI','ATENDIMENTO'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS app_auditoria (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    usuario_id BIGINT UNSIGNED NOT NULL,
    cliente_id BIGINT UNSIGNED NULL,
    acao VARCHAR(40) NOT NULL,
    entidade VARCHAR(30) NOT NULL,
    entidade_id BIGINT UNSIGNED NOT NULL,
    antes LONGTEXT NULL,
    depois LONGTEXT NULL,
    motivo VARCHAR(500) NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_app_auditoria_cliente (cliente_id,id),
    CONSTRAINT fk_app_auditoria_usuario FOREIGN KEY (usuario_id) REFERENCES app_usuarios(id),
    CONSTRAINT fk_app_auditoria_cliente FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS app_login_tentativas (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    login VARCHAR(80) NOT NULL,
    origem VARCHAR(64) NOT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_app_tentativas_login (login,criado_em),
    INDEX idx_app_tentativas_origem (origem,criado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
