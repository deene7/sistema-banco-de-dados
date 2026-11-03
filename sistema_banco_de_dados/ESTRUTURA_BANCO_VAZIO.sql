-- Estrutura vazia: sem clientes, contatos, usuários ou credenciais.
CREATE DATABASE IF NOT EXISTS wa_clientes CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE wa_clientes;

CREATE TABLE `clientes` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `cpf` varchar(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `nome` varchar(255) DEFAULT NULL,
  `data_nascimento` date DEFAULT NULL,
  `criado_em` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `atualizado_em` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `excluido_em` datetime DEFAULT NULL,
  `nome_mae` varchar(255) DEFAULT NULL,
  `sexo_origem` varchar(1) DEFAULT NULL,
  `app_versao` bigint unsigned NOT NULL DEFAULT '1',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_clientes_cpf` (`cpf`),
  KEY `idx_app_nome` (`nome`),
  CONSTRAINT `chk_clientes_cpf_formato` CHECK (regexp_like(`cpf`,_ascii'^[0-9]{11}$'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `telefones` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `cliente_id` bigint unsigned NOT NULL,
  `numero` varchar(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `situacao` varchar(20) NOT NULL DEFAULT 'nao_verificado',
  `criado_em` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `atualizado_em` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `excluido_em` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_telefones_cliente_numero` (`cliente_id`,`numero`),
  KEY `idx_telefones_numero` (`numero`),
  CONSTRAINT `fk_telefones_cliente` FOREIGN KEY (`cliente_id`) REFERENCES `clientes` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `chk_telefones_numero` CHECK (regexp_like(`numero`,_utf8mb4'^[0-9]{10,11}$')),
  CONSTRAINT `chk_telefones_situacao` CHECK ((`situacao` in (_utf8mb4'nao_verificado',_utf8mb4'confirmado',_utf8mb4'invalido',_utf8mb4'inativo')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `emails` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `cliente_id` bigint unsigned NOT NULL,
  `email` varchar(320) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `situacao` varchar(20) NOT NULL DEFAULT 'nao_verificado',
  `criado_em` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `excluido_em` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_emails_cliente_email` (`cliente_id`,`email`),
  CONSTRAINT `fk_emails_cliente` FOREIGN KEY (`cliente_id`) REFERENCES `clientes` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `enderecos` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `cliente_id` bigint unsigned NOT NULL,
  `tipo` varchar(100) COLLATE utf8mb4_bin DEFAULT NULL,
  `titulo` varchar(100) COLLATE utf8mb4_bin DEFAULT NULL,
  `logradouro` varchar(255) COLLATE utf8mb4_bin DEFAULT NULL,
  `numero` varchar(50) COLLATE utf8mb4_bin DEFAULT NULL,
  `complemento` varchar(255) COLLATE utf8mb4_bin DEFAULT NULL,
  `bairro` varchar(150) COLLATE utf8mb4_bin DEFAULT NULL,
  `cidade` varchar(150) COLLATE utf8mb4_bin DEFAULT NULL,
  `uf` varchar(2) COLLATE utf8mb4_bin DEFAULT NULL,
  `cep` varchar(8) COLLATE utf8mb4_bin DEFAULT NULL,
  `criado_em` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `excluido_em` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_enderecos_cliente` (`cliente_id`),
  CONSTRAINT `fk_enderecos_cliente` FOREIGN KEY (`cliente_id`) REFERENCES `clientes` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
