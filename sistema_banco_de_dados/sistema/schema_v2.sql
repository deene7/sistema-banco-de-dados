CREATE TABLE IF NOT EXISTS app_categorias (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 nome VARCHAR(100) NOT NULL UNIQUE,
 descricao VARCHAR(500) NULL,
 ativo TINYINT NOT NULL DEFAULT 1,
 versao BIGINT UNSIGNED NOT NULL DEFAULT 1,
 criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS app_cliente_categorias (
 cliente_id BIGINT UNSIGNED NOT NULL,
 categoria_id BIGINT UNSIGNED NOT NULL,
 criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(cliente_id,categoria_id),
 INDEX idx_app_categoria_clientes(categoria_id,cliente_id),
 FOREIGN KEY(cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
 FOREIGN KEY(categoria_id) REFERENCES app_categorias(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS app_importacoes (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 token VARCHAR(40) NOT NULL UNIQUE,
 arquivo VARCHAR(255) NOT NULL,
 caminho VARCHAR(255) NOT NULL,
 sha256 VARCHAR(64) NOT NULL,
 categoria_id BIGINT UNSIGNED NULL,
 usuario_id BIGINT UNSIGNED NOT NULL,
 aprovador_id BIGINT UNSIGNED NULL,
 estado VARCHAR(30) NOT NULL DEFAULT 'mapeamento',
 aba VARCHAR(150) NULL,
 mapeamento LONGTEXT NULL,
 progresso INT NOT NULL DEFAULT 0,
 total_linhas INT NOT NULL DEFAULT 0,
 resumo LONGTEXT NULL,
 erro VARCHAR(1000) NULL,
 excluido_em DATETIME NULL,
 criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 atualizado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 INDEX idx_app_importacao_hash(sha256,categoria_id),
 FOREIGN KEY(categoria_id) REFERENCES app_categorias(id),
 FOREIGN KEY(usuario_id) REFERENCES app_usuarios(id),
 FOREIGN KEY(aprovador_id) REFERENCES app_usuarios(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS app_importacao_itens (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 importacao_id BIGINT UNSIGNED NOT NULL,
 chave VARCHAR(40) NOT NULL,
 cpf VARCHAR(11) NULL,
 estado VARCHAR(20) NOT NULL,
 dados LONGTEXT NOT NULL,
 originais LONGTEXT NOT NULL,
 linhas LONGTEXT NOT NULL,
 motivos LONGTEXT NULL,
 cliente_versao BIGINT UNSIGNED NULL,
 cliente_id_resultado BIGINT UNSIGNED NULL,
 novo_cliente INT NOT NULL DEFAULT 0,
 novos_telefones INT NOT NULL DEFAULT 0,
 novos_emails INT NOT NULL DEFAULT 0,
 novos_enderecos INT NOT NULL DEFAULT 0,
 nova_categoria INT NOT NULL DEFAULT 0,
 UNIQUE(importacao_id,chave),
 INDEX idx_app_importacao_estado(importacao_id,estado,id),
 INDEX idx_app_importacao_cpf(cpf,estado,id),
 FOREIGN KEY(importacao_id) REFERENCES app_importacoes(id),
 CONSTRAINT fk_app_importacao_item_cliente FOREIGN KEY(cliente_id_resultado) REFERENCES clientes(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
