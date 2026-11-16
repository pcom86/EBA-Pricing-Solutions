USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[run]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[run](
	[run_id] [uniqueidentifier] NOT NULL,
	[scheme_id] [uniqueidentifier] NOT NULL,
	[ctr_fingerprint] [nvarchar](64) NOT NULL,
	[basis_snapshot] [nvarchar](100) NULL,
	[assumption_snapshot] [nvarchar](100) NULL,
	[s3_path] [nvarchar](500) NULL,
	[status] [nvarchar](20) NOT NULL,
	[row_count] [int] NULL,
	[error_detail] [nvarchar](max) NULL,
	[started_at] [datetime2](7) NULL,
	[completed_at] [datetime2](7) NULL,
	[basis_bundle_hash] [nvarchar](64) NULL,
	[assumption_bundle_hash] [nvarchar](64) NULL,
	[rerun_number] [int] NOT NULL,
	[engine_version_hash] [nvarchar](64) NULL,
PRIMARY KEY CLUSTERED 
(
	[run_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO
ALTER TABLE [dbo].[run] ADD  DEFAULT ('queued') FOR [status]
GO
ALTER TABLE [dbo].[run] ADD  DEFAULT ((1)) FOR [rerun_number]
GO
ALTER TABLE [dbo].[run]  WITH CHECK ADD FOREIGN KEY([scheme_id])
REFERENCES [dbo].[scheme] ([scheme_id])
GO
