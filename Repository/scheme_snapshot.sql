USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[scheme_snapshot]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[scheme_snapshot](
	[run_id] [uniqueidentifier] NOT NULL,
	[scheme_id] [uniqueidentifier] NOT NULL,
	[scheme_record_json] [nvarchar](max) NOT NULL,
	[active_products_json] [nvarchar](max) NOT NULL,
	[active_categories_json] [nvarchar](max) NOT NULL,
	[warnings_json] [nvarchar](max) NULL,
	[ctr_loaded_at] [nvarchar](64) NULL,
	[created_at] [datetime2](7) NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[run_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO
ALTER TABLE [dbo].[scheme_snapshot] ADD  DEFAULT (sysutcdatetime()) FOR [created_at]
GO
ALTER TABLE [dbo].[scheme_snapshot]  WITH CHECK ADD  CONSTRAINT [FK_scheme_snapshot_run] FOREIGN KEY([run_id])
REFERENCES [dbo].[run] ([run_id])
GO
ALTER TABLE [dbo].[scheme_snapshot] CHECK CONSTRAINT [FK_scheme_snapshot_run]
GO
ALTER TABLE [dbo].[scheme_snapshot]  WITH CHECK ADD  CONSTRAINT [FK_scheme_snapshot_scheme] FOREIGN KEY([scheme_id])
REFERENCES [dbo].[scheme] ([scheme_id])
GO
ALTER TABLE [dbo].[scheme_snapshot] CHECK CONSTRAINT [FK_scheme_snapshot_scheme]
GO
ALTER TABLE [dbo].[scheme_snapshot]  WITH CHECK ADD  CONSTRAINT [CK_scheme_snapshot_categories_json] CHECK  ((isjson([active_categories_json])=(1)))
GO
ALTER TABLE [dbo].[scheme_snapshot] CHECK CONSTRAINT [CK_scheme_snapshot_categories_json]
GO
ALTER TABLE [dbo].[scheme_snapshot]  WITH CHECK ADD  CONSTRAINT [CK_scheme_snapshot_json] CHECK  ((isjson([scheme_record_json])=(1)))
GO
ALTER TABLE [dbo].[scheme_snapshot] CHECK CONSTRAINT [CK_scheme_snapshot_json]
GO
ALTER TABLE [dbo].[scheme_snapshot]  WITH CHECK ADD  CONSTRAINT [CK_scheme_snapshot_products_json] CHECK  ((isjson([active_products_json])=(1)))
GO
ALTER TABLE [dbo].[scheme_snapshot] CHECK CONSTRAINT [CK_scheme_snapshot_products_json]
GO
ALTER TABLE [dbo].[scheme_snapshot]  WITH CHECK ADD  CONSTRAINT [CK_scheme_snapshot_warnings_json] CHECK  (([warnings_json] IS NULL OR isjson([warnings_json])=(1)))
GO
ALTER TABLE [dbo].[scheme_snapshot] CHECK CONSTRAINT [CK_scheme_snapshot_warnings_json]
GO
